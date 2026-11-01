import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch

from analysis.scanner_reference_data import (ET,Providers,ReferenceLoader,normalize_massive_float,
    build_profile,baseline_for)
from analysis.scanner_inputs import AggregateWindows,float_reading
from analysis.live_tape import TapeBook,LiveTape


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.target='2026-11-02'
        start=datetime(2026,10,13)
        self.sessions=[(start+timedelta(days=i)).date().isoformat() for i in range(18)
                       if (start+timedelta(days=i)).weekday()<5]
        self.assertEqual(len(self.sessions),14)
        self.now=datetime(2026,11,1,15,tzinfo=ET).timestamp()

    def bars(self):
        return [{'t':int(datetime.fromisoformat(s).replace(hour=7,minute=m,tzinfo=ET).timestamp()*1000),'v':100}
                for s in self.sessions for m in range(5)]

    def profile(self):return build_profile(self.bars(),self.sessions,self.target,self.now)

    def test_dated_float_does_not_become_historical_or_outstanding(self):
        rows=[{'ticker':'TEST','effective_date':'2026-10-30','free_float':400000}]
        record=normalize_massive_float(rows,'TEST',self.now)
        self.assertEqual(float_reading(record,self.now)['value'],400000)
        self.assertEqual(float_reading(record,self.now-1)['status'],'unavailable')
        record['conflict']='Different provider values'
        self.assertIsNone(float_reading(record,self.now)['qualifies_under_20m'])
        for row in ({**rows[0],'effective_date':'2026-12-01'},
                    {'ticker':'TEST','effective_date':'2026-10-30','outstandingShares':500000}):
            with self.assertRaises(ValueError):normalize_massive_float([row],'TEST',self.now)

    def test_float_disagreement_keeps_both_values(self):
        provider=Providers('not-a-real-key','not-a-real-fmp-key')
        with patch.object(provider,'pages',return_value=[{'ticker':'TEST','effective_date':'2026-10-30','free_float':500000}]), \
             patch.object(provider,'get',return_value=[{'symbol':'TEST','floatShares':200000,'date':'2026-11-01 17:00:00','source':'https://www.sec.gov/example'}]):
            result=provider.load_float('TEST')
        self.assertEqual(result['value'],500000)
        self.assertEqual(result['alternate']['value'],200000)
        self.assertIn('conflict',result)

    def test_same_clock_across_daylight_saving_and_no_future_baseline(self):
        profile=self.profile()
        end=datetime(2026,11,2,7,5,tzinfo=ET).timestamp()
        baseline=baseline_for(profile,end)
        self.assertEqual(baseline['mean_volume'],500)
        self.assertEqual(baseline['sample_sessions'],14)
        self.assertIsNone(baseline_for(profile,end+1))
        self.assertIsNone(baseline_for(profile,end+86400))
        with self.assertRaises(ValueError):build_profile(self.bars(),self.sessions,self.sessions[-1],self.now)

    def test_missing_session_and_conflicting_duplicate_are_holes(self):
        bars=self.bars()
        with self.assertRaisesRegex(ValueError,'Entire baseline sessions'):build_profile(bars[5:],self.sessions,self.target,self.now)
        with self.assertRaisesRegex(ValueError,'Conflicting duplicate'):build_profile(bars+[{**bars[0],'v':99}],self.sessions,self.target,self.now)
        self.assertEqual(build_profile(bars+[bars[0]],self.sessions,self.target,self.now)['bar_count'],70)

    def test_pagination_failure_does_not_return_partial_history(self):
        p=Providers('fixture')
        with patch.object(p,'get',return_value={'results':[1],'next_url':'https://api.massive.com/repeat'}):
            with self.assertRaisesRegex(RuntimeError,'Repeated pagination'):p.pages('https://api.massive.com/repeat')

    def test_split_rejects_history_without_discarding_float(self):
        p=Providers('fixture')
        with patch.object(p,'load_float',return_value={'value':100}), \
             patch.object(p,'get',return_value={'results':{'list_date':'2020-01-01'}}), \
             patch.object(p,'pages',return_value=[{'execution_date':'2026-10-20'}]):
            result=p.load('TEST',self.target,self.sessions)
        self.assertEqual(result['float']['value'],100)
        self.assertIsNone(result['history'])
        self.assertIn('Split',result['errors']['history'])

    def test_loaded_history_reaches_scanner_without_lookahead(self):
        book=TapeBook();start=datetime(2026,11,2,7,tzinfo=ET).timestamp()
        record=normalize_massive_float([{'ticker':'TEST','free_float':400000,'effective_date':'2026-10-30'}],'TEST',self.now)
        book.apply_references({'ticker':'TEST','float':record,'history':self.profile(),'errors':{}})
        book.ingest({'ev':'T','sym':'TEST','t':start*1000,'q':1,'s':100,'p':5,'c':[12]},start+.1)
        for i in range(310):
            book.ingest({'ev':'A','sym':'TEST','s':(start+i)*1000,'e':(start+i)*1000+999,'v':10},start+i+1)
        row=book.snapshot(start+310,'TEST')['selected']
        self.assertEqual(row['rvol_5m']['ratio'],6)
        self.assertEqual(row['rvol_5m']['current']['volume'],3000)  # ignores unfinished next minute
        self.assertEqual(row['rvol_5m']['current']['as_of'],start+300)
        self.assertTrue(row['float_reference']['qualifies_under_20m'])
        self.assertIsNone(book.snapshot(start+376,'TEST')['selected']['rvol_5m']['ratio'])
        book.reset_windows()
        self.assertIsNone(book.snapshot(start+377,'TEST')['selected']['rvol_5m']['ratio'])

    def test_queue_cap_retry_and_cache(self):
        p=Providers('fixture');loader=ReferenceLoader(p,max_symbols=2)
        loader.request(['AAA','BBB','CCC'],self.target,self.now)
        self.assertEqual(loader.pending,['AAA','BBB']);self.assertEqual(loader.diagnostics()['deferred_count'],1)
        loader.pending.pop(0);loader.attempted['AAA']=self.now
        loader.request(['AAA'],self.target,self.now+1);self.assertNotIn('AAA',loader.pending)
        loader.request(['AAA'],self.target,self.now+1801);self.assertIn('AAA',loader.pending)
        result={'ticker':'AAA','target_session':self.target,'retrieved_at':self.now,'float':{},'history':{},'errors':{}}
        scratch=Path(__file__).resolve().parents[1]/'tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder,patch('analysis.scanner_reference_data.time.time',return_value=self.now+1):
            loader=ReferenceLoader(p,folder)
            with patch.object(p,'sessions_before',return_value=self.sessions),patch.object(p,'load',return_value=result) as call:
                loader.fetch_one('AAA',self.target);loader.fetch_one('AAA',self.target)
                self.assertEqual(call.call_count,1)

    def test_new_selected_stock_moves_ahead_of_queued_names(self):
        loader=ReferenceLoader(Providers('fixture'))
        loader.request(['AAA','BBB','CCC'],self.target,self.now)
        loader.request(['NEW','CCC'],self.target,self.now+1)
        self.assertEqual(loader.pending,['NEW','CCC','AAA','BBB'])

    def test_old_effective_date_is_not_stale_receipt_or_a_conflict(self):
        record=normalize_massive_float([{'ticker':'TEST','free_float':400000,'effective_date':'2026-05-01'}],'TEST',self.now)
        reading=float_reading(record,self.now)
        self.assertEqual(reading['status'],'available')
        self.assertTrue(reading['age_warning'])
        self.assertTrue(reading['qualifies_under_20m'])
        record['review_reason']='Split occurred after float measurement'
        reading=float_reading(record,self.now)
        self.assertEqual(reading['status'],'review')
        self.assertTrue(reading['reported_under_20m'])
        self.assertIsNone(reading['qualifies_under_20m'])
        record.pop('review_reason');record['checked_at']=self.now+1
        self.assertEqual(float_reading(record,self.now)['status'],'unavailable')
        self.assertIsNone(float_reading(record,self.now+86401)['qualifies_under_20m'])

    def test_bulk_float_catalog_reuses_values_but_checks_corporate_actions(self):
        provider=Providers('fixture')
        rows=[{'ticker':'TEST','free_float':400000,'effective_date':'2026-05-01'},
              {'ticker':'BAD','free_float':None,'effective_date':'2026-10-30'}]
        with patch.object(provider,'pages',return_value=rows):catalog=provider.load_float_catalog()
        self.assertEqual(len(catalog['records']),1)
        self.assertEqual(len(catalog['invalid']),1)
        self.assertEqual(float_reading(catalog['records']['TEST'],catalog['received_at'])['status'],'review')
        with patch.object(provider,'pages',return_value=[]) as calls:
            checked=provider.load_float('TEST')
        self.assertEqual(calls.call_count,1)
        self.assertIn('/reference/splits?',calls.call_args.args[0])
        self.assertNotIn('review_reason',checked)
        self.assertNotIn('catalog_only',checked)
        self.assertIn('checked_at',checked)

    def test_failed_catalog_refresh_keeps_previous_dated_catalog(self):
        provider=Providers('fixture');provider.float_catalog={'OLD':{'value':123}}
        provider.float_catalog_at=self.now
        with patch.object(provider,'pages',side_effect=RuntimeError('Pagination failed')):
            with self.assertRaises(RuntimeError):provider.load_float_catalog()
        self.assertEqual(provider.float_catalog_at,self.now)
        self.assertEqual(provider.float_catalog['OLD']['value'],123)

    def test_catalog_archive_uses_receipt_date_not_effective_date(self):
        scratch=Path(__file__).resolve().parents[1]/'tmp';scratch.mkdir(exist_ok=True)
        result={'received_at':self.now,'records':{'TEST':{'effective_date_raw':'2026-05-01','value':1000}}}
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            loader=ReferenceLoader(Providers('fixture'),folder)
            self.assertEqual(loader.persist_catalog(result)['status'],'saved')
            paths=list(Path(folder).glob('float_catalog/2026-11-01/*.json'))
            self.assertEqual(len(paths),1)
            self.assertEqual(json.loads(paths[0].read_text()),result)
            self.assertEqual(loader.persist_catalog(result)['status'],'unavailable')  # no overwrite

    def test_later_reference_cannot_rewrite_an_old_alert(self):
        book=TapeBook();start=datetime(2026,11,2,7,tzinfo=ET).timestamp()
        for i in range(302):
            book.ingest({'ev':'T','sym':'TEST','t':(start+i)*1000,'q':i+1,'s':100,
                'p':5 if i<300 else 5.5,'c':[12]},start+i+.1)
        self.assertTrue(book.alerts)
        old=book.alerts[0]
        self.assertEqual(old['reference_context']['float_reference']['status'],'unavailable')
        record=normalize_massive_float([{'ticker':'TEST','free_float':400000,'effective_date':'2026-10-30'}],'TEST',start+302)
        book.apply_references({'ticker':'TEST','float':record,'history':self.profile(),'errors':{}})
        self.assertTrue(book.snapshot(start+303,'TEST')['selected']['float_reference']['qualifies_under_20m'])
        self.assertEqual(old['reference_context']['float_reference']['status'],'unavailable')


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_worker_applies_result_and_stops(self):
        import time
        now=time.time();target=datetime.fromtimestamp(now,ET).date().isoformat()
        stream=LiveTape();stream.selected='TEST'
        result={'ticker':'TEST','target_session':target,'float':{'value':400000},'history':None,'errors':{}}
        p=Providers('fixture');stream.scanner_loader=ReferenceLoader(p)
        with patch.object(stream,'enabled',return_value=True), \
             patch.object(p,'sessions_before',return_value=['fixture']), \
             patch.object(p,'load_float_catalog',return_value={'records':{},'received_at':now,'invalid':[]}), \
             patch.object(p,'load',return_value=result):
            task=asyncio.create_task(stream.load_scanner_references())
            try:
                for _ in range(100):
                    if 'TEST' in stream.book.float_references:break
                    await asyncio.sleep(.01)
                self.assertEqual(stream.book.float_references['TEST']['value'],400000)
                self.assertEqual(stream.scanner_loader.diagnostics()['loaded_count'],1)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task


if __name__=='__main__':unittest.main()
