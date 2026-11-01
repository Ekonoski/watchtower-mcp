import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from analysis.live_tape import TapeBook
from analysis.scanner_journal import ScannerJournal, TapeLifespan


class Store:
    def __init__(self):self.rows={};self.fail=False
    def write(self, rows):
        for row in rows:self.rows.setdefault(row['id'],row)
        if self.fail:raise RuntimeError('Acknowledgement lost after commit')


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_alerts_survive_ui_rolloff_and_freeze_context(self):
        store=Store();journal=ScannerJournal(store)
        book=TapeBook(lambda a:journal.record('alert',a,a['received_at']))
        start=1793104800
        for i in range(301):
            for symbol in range(160):
                book.ingest({'ev':'T','sym':f'T{symbol}','p':5 if i<300 else 5.26,
                    't':(start+i)*1000,'s':100,'q':i+1,'c':[12]},start+i+.1)
        self.assertEqual(len(book.alerts),150)
        self.assertEqual(len(journal.pending),160)
        book.alerts[0]['reference_context']['inputs_observed_at']=0
        await journal.flush()
        self.assertEqual(len(store.rows),160)
        self.assertTrue(all(r['payload']['reference_context']['inputs_observed_at']>0 for r in store.rows.values()))
        self.assertTrue(all(r['payload']['direction']=='up' for r in store.rows.values()))

    async def test_retry_preserves_ids_and_does_not_duplicate_after_uncertain_commit(self):
        store=Store();journal=ScannerJournal(store)
        journal.record('alert',{'direction':'down'},1793104800)
        store.fail=True;await journal.flush()
        self.assertEqual(len(journal.pending),1)
        self.assertEqual(journal.status()['state'],'unavailable')
        store.fail=False;await journal.flush()
        self.assertEqual(len(store.rows),1)
        self.assertEqual(journal.saved,1)
        self.assertEqual(journal.failures,1)

    async def test_queue_capacity_is_visible(self):
        journal=ScannerJournal(Store(),capacity=1)
        journal.record('alert',{});journal.record('alert',{})
        self.assertEqual(journal.dropped,1)
        self.assertEqual(len(journal.pending),1)

    async def test_run_identity_and_et_date(self):
        a=ScannerJournal(Store());b=ScannerJournal(Store())
        self.assertNotEqual(a.run_id,b.run_id)
        from datetime import datetime
        at=datetime.fromisoformat('2026-11-02T01:00:00+00:00').timestamp()
        a.record('alert',{},at)
        self.assertEqual(a.pending[0]['session_date'],'2026-11-01')

    async def test_lifespan_starts_and_stops_without_browser_request(self):
        tape=SimpleNamespace(ensure_started=AsyncMock(),stop=AsyncMock())
        async def app(scope,receive,send):
            await send({'type':'lifespan.startup.complete'})
            await send({'type':'lifespan.shutdown.complete'})
        sent=[]
        async def send(message):sent.append(message)
        await TapeLifespan(app,tape)({'type':'lifespan'},AsyncMock(),send)
        tape.ensure_started.assert_awaited_once();tape.stop.assert_awaited_once()
        self.assertEqual(len(sent),2)

    async def test_idle_heartbeat_does_not_claim_trade_coverage(self):
        journal=ScannerJournal(Store())
        tape=SimpleNamespace(state='quiet',reason='No market messages',book=TapeBook())
        task=asyncio.create_task(journal.run(tape))
        await asyncio.sleep(.05);journal.stopping=True;await task
        rows=list(journal.store.rows.values())
        beat=next(r['payload'] for r in rows if r['kind']=='heartbeat')
        self.assertIsNone(beat['market_at'])
        self.assertIsNone(beat['warm_since'])
        self.assertEqual(beat['alert_total'],0)
        self.assertTrue(any(r['kind']=='run_stop' for r in rows))


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_auth_validation_and_failure(self):
        from tests.test_trading_workspace_api import Registry
        from dashboard import api
        from starlette.requests import Request
        registry=Registry();api.register_routes(registry)
        fn=registry.routes['/api/trading/history']
        def req(q=b''):
            return Request({'type':'http','method':'GET','path':'/api/trading/history','query_string':q,'headers':[]})
        with patch.object(api,'_is_authed',return_value=False),patch('analysis.scanner_journal.PostgresJournal.export') as export:
            self.assertEqual((await fn(req())).status_code,401);export.assert_not_called()
        with patch.object(api,'_is_authed',return_value=True):
            self.assertEqual((await fn(req(b'session=bad'))).status_code,400)
            with patch('analysis.scanner_journal.PostgresJournal.export',side_effect=RuntimeError('storage offline')):
                self.assertEqual((await fn(req(b'session=2026-11-01'))).status_code,503)
            with patch('analysis.scanner_journal.PostgresJournal.export',return_value={'total':5001,'next_offset':5000}):
                result=await fn(req(b'session=2026-11-01'))
                self.assertEqual(json.loads(result.body)['next_offset'],5000)


if __name__=='__main__':unittest.main()
