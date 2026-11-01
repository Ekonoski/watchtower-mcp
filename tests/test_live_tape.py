import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analysis.live_tape import TapeBook,LiveTape

class TapeTests(unittest.TestCase):
    def setUp(self):self.book=TapeBook();self.t=1793104800
    def trade(self,seconds,price=5,symbol='TEST',q=None,conditions=None):
        return {'ev':'T','sym':symbol,'p':price,'t':(self.t+seconds)*1000,'s':100,
                'q':q if q is not None else int(seconds*1000)+1,'c':[12] if conditions is None else conditions}
    def put(self,seconds,price=5,**kwargs):return self.book.ingest(self.trade(seconds,price,**kwargs),self.t+seconds+.1)
    def test_unknown_windows_are_not_zero(self):
        self.put(0)
        self.assertIsNone(self.book.rows['TEST']['move_1m'])
        self.assertIsNone(self.book.rows['TEST']['chg_pct'])
    def test_causal_move_and_threshold_cross(self):
        for i in range(301):self.put(i,5 if i<300 else 5.26)
        self.assertAlmostEqual(self.book.rows['TEST']['move_5m'],5.2)
        self.assertEqual(len(self.book.alerts),1)
        self.put(301,5.27)
        self.assertEqual(len(self.book.alerts),1)
    def test_downside_is_measured_symmetrically(self):
        for i in range(301):self.put(i,5 if i<300 else 4.74)
        self.assertEqual(self.book.alerts[0]['direction'],'down')
    def test_invalid_and_late_prints_cannot_move_price(self):
        self.put(1)
        for codes in ([13],[2],[44],[60]):self.assertFalse(self.put(2,50,conditions=codes))
        self.assertFalse(self.book.ingest(self.trade(100,50),self.t+1))
        self.assertFalse(self.book.ingest(self.trade(2,50),self.t+100))
        self.assertEqual(self.book.rows['TEST']['price'],5)
    def test_duplicate_sequence_and_same_millisecond(self):
        self.put(1,q=5)
        self.assertFalse(self.put(1,5.5,q=5))
        self.assertTrue(self.put(1,5.2,q=6))
        self.assertEqual(len(self.book.history['TEST']),1)
        self.assertEqual(self.book.history['TEST'][0][4],5)
        self.assertEqual(self.book.rows['TEST']['price'],5.2)
    def test_aggregate_volume_cannot_replace_fast_price(self):
        self.put(5,5.2)
        self.book.ingest({'ev':'A','sym':'TEST','e':(self.t+3)*1000,'c':1,'av':1000},self.t+5.1)
        row=self.book.snapshot(self.t+5.1,'TEST')['selected']
        self.assertEqual(row['price'],5.2)
        self.assertEqual(row['volume'],1000)
        self.assertEqual(row['volume_market_at'],self.t+3)
        self.book.ingest({'ev':'A','sym':'TEST','e':(self.t+2)*1000,'av':800},self.t+5.1)
        self.assertEqual(self.book.snapshot(self.t+5.1,'TEST')['selected']['volume'],1000)
    def test_invalid_next_day_cannot_clear_sequence_guard(self):
        self.put(1,q=5)
        self.assertFalse(self.book.ingest(self.trade(86401,50,q=1),self.t+2))
        self.assertFalse(self.put(1,50,q=5))
        self.assertEqual(self.book.rows['TEST']['price'],5)
    def test_zero_quantity_and_bad_conditions_do_not_move_price(self):
        self.put(1)
        e=self.trade(2,50);e['s']=0
        self.assertFalse(self.book.ingest(e,self.t+2))
        e['s']=1;e['c']=[{}]
        self.assertFalse(self.book.ingest(e,self.t+2))
        self.assertEqual(self.book.rows['TEST']['price'],5)
    def test_negative_clock_difference_remains_visible(self):
        self.book.ingest(self.trade(1),self.t+.9)
        self.assertAlmostEqual(self.book.snapshot(self.t+2)['lag_p50_ms'],-100,places=2)
    def test_stale_rows_retained_only_in_focus_with_timestamp(self):
        self.put(0)
        snap=self.book.snapshot(self.t+20,'TEST')
        self.assertEqual(snap['rows'],[])
        self.assertEqual(snap['selected']['market_at'],self.t)
    def test_reconnect_resets_windows(self):
        for i in range(61):self.put(i)
        self.assertEqual(self.book.rows['TEST']['move_1m'],0)
        self.book.reset_windows();self.put(62)
        self.assertIsNone(self.book.rows['TEST']['move_1m'])
    def test_missing_window_reference_stays_unknown(self):
        self.put(0,5)
        self.put(100,6)
        self.assertIsNone(self.book.rows['TEST']['move_1m'])
        self.put(160,6.6)
        self.assertAlmostEqual(self.book.rows['TEST']['move_1m'],10)
    def test_previous_day_late_trade_cannot_rewind_session(self):
        from datetime import datetime
        from analysis.live_tape import ET
        midnight=datetime(2026,10,27,tzinfo=ET).timestamp()
        first=self.trade(0);first['t']=midnight*1000
        self.book.ingest(first,midnight+.1)
        prior=self.trade(0,50,q=100);prior['t']=(midnight-1)*1000
        self.assertFalse(self.book.ingest(prior,midnight+.2))
        self.assertEqual(self.book.rows['TEST']['price'],5)
    def test_session_boundary_clears_previous_prices(self):
        self.put(1)
        self.put(86401,6,symbol='NEXT')
        self.assertNotIn('TEST',self.book.rows)
        self.assertIsNone(self.book.rows['NEXT']['move_1m'])
    def test_truncation_is_explicit(self):
        for i in range(110):self.put(1,symbol='S'+str(i))
        snap=self.book.snapshot(self.t+2)
        self.assertEqual(len(snap['rows']),100)
        self.assertEqual(snap['omitted_count'],10)
    def test_stream_is_opt_in_and_has_single_owner_task(self):
        async def check():
            stream=LiveTape()
            with patch.dict(os.environ,{'WATCHTOWER_LIVE_TAPE':''}):
                await stream.ensure_started();self.assertIsNone(stream.task)
            async def idle():await asyncio.sleep(20)
            with patch.object(stream,'_run',idle),patch.dict(os.environ,{'WATCHTOWER_LIVE_TAPE':'1'}):
                await stream.ensure_started();a=stream.task
                await stream.ensure_started();self.assertIs(a,stream.task)
                a.cancel()
                try:await a
                except asyncio.CancelledError:pass
        asyncio.run(check())
    def test_recorded_trade_capture_has_no_future_leak(self):
        p=Path(__file__).resolve().parents[1]/'docs/research/watchtower_trading_workspace_20261027/trade_stream_events.json'
        if not p.exists():self.skipTest('Local capture absent')
        for item in json.loads(p.read_text()):self.book.ingest(item['event'],item['received_at'])
        self.assertGreater(len(self.book.rows),100)
        self.assertTrue(all(r['market_at']<=r['received_at'] for r in self.book.rows.values()))
        self.assertTrue(all(r['move_1m'] is None for r in self.book.rows.values()))

if __name__=='__main__':unittest.main()
