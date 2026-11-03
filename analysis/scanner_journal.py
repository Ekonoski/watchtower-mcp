"""Server-side scanner evidence. Buffered writes never block market ingestion.

Only committed rows survive a crash. Heartbeats, run IDs and explicit loss
counts delimit coverage; absence of an alert is not proof of complete capture.
"""
import asyncio
import copy
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo
from analysis.scanner_journal_codec import CODEC, MARKER, PACKED_KINDS, pack, restore

ET = ZoneInfo('America/New_York')
RULES = 'observed-trades-5pct5m-10pct10m-v1'
log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS public.scanner_journal (
 id text PRIMARY KEY, run_id text NOT NULL, kind text NOT NULL,
 session_date date NOT NULL, observed_at timestamptz NOT NULL,
 saved_at timestamptz NOT NULL DEFAULT clock_timestamp(), payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS scanner_journal_session_time
 ON public.scanner_journal(session_date, observed_at, id);
ALTER TABLE public.scanner_journal ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.scanner_journal FROM anon, authenticated;
"""

def connection():
    from screen.reversal_screen import _conn
    return _conn()


class PostgresJournal:
    def write(self, records):
        from psycopg2.extras import execute_values
        from psycopg2 import Binary
        if not records:return
        conn = connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '10s'")
                # Acknowledgement can be lost after commit. Exclude durable IDs
                # before packing, even if the retry now includes newer records.
                cur.execute('SELECT id FROM public.scanner_journal WHERE id=ANY(%s)',
                            ([r['id'] for r in records],))
                existing = {r[0] for r in cur.fetchall()}
                records = [r for r in records if r['id'] not in existing]
                if not records:return
                bulky = [r for r in records if r['kind'] in PACKED_KINDS]
                chunk = pack(bulky) if bulky else None
                if chunk:
                    cur.execute('''INSERT INTO public.scanner_journal_chunks
                        (id,codec,sha256,raw_bytes,record_count,body)
                        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING''',
                        (chunk['id'],chunk['codec'],chunk['sha256'],chunk['raw_bytes'],
                         chunk['record_count'],Binary(chunk['body'])))
                execute_values(cur, """INSERT INTO public.scanner_journal
                    (id,run_id,kind,session_date,observed_at,payload,payload_chunk_id) VALUES %s
                    ON CONFLICT (id) DO NOTHING""", [
                    (r['id'],r['run_id'],r['kind'],r['session_date'],
                     datetime.fromtimestamp(r['observed_at'],ET),
                     json.dumps(MARKER if r['kind'] in PACKED_KINDS else r['payload'],allow_nan=False),
                     chunk['id'] if r['kind'] in PACKED_KINDS else None)
                    for r in records])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def export(self, session, offset=0, limit=5000):
        conn = connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '10s'")
                cur.execute("SELECT count(*) FROM public.scanner_journal WHERE session_date=%s",(session,))
                total=cur.fetchone()[0]
                cur.execute("""SELECT id,run_id,kind,observed_at,saved_at,payload,payload_chunk_id::text
                    FROM public.scanner_journal WHERE session_date=%s
                    ORDER BY observed_at,id LIMIT %s OFFSET %s""",(session,limit,offset))
                rows=[dict(id=r[0],run_id=r[1],kind=r[2],observed_at=r[3].isoformat(),
                           saved_at=r[4].isoformat(),payload=r[5],payload_chunk_id=r[6]) for r in cur.fetchall()]
                chunk_ids = list({r['payload_chunk_id'] for r in rows if r['payload_chunk_id']})
                chunks = {}
                if chunk_ids:
                    cur.execute('''SELECT id::text,codec,sha256,raw_bytes,record_count,body
                        FROM public.scanner_journal_chunks WHERE id=ANY(%s::uuid[])''',(chunk_ids,))
                    chunks = {r[0]:dict(id=r[0],codec=r[1],sha256=r[2],raw_bytes=r[3],
                                       record_count=r[4],body=r[5]) for r in cur.fetchall()}
                restore(rows,chunks)
            return {'session':session,'total':total,'offset':offset,'records':rows,
                    'next_offset':offset+len(rows) if offset+len(rows)<total else None,
                    'note':'Server observations, not browser display latency. Pagination is stable after the session settles. Coverage must be checked from heartbeats, connection events and run boundaries; crashes can lose uncommitted queued rows.'}
        finally:
            conn.close()


class ScannerJournal:
    def __init__(self, store=None, capacity=20000, byte_capacity=32*1024*1024):
        self.store=store or PostgresJournal()
        self.run_id=str(uuid.uuid4())
        self.pending=deque()
        self.capacity=capacity
        self.byte_capacity=byte_capacity
        self.pending_bytes=0
        self.sequence=0
        self.saved=0
        self.dropped=0
        self.last_saved_at=None
        self.error=None
        self.last_failure_at=None
        self.failures=0
        self.task=None
        self.stopping=False
        self.list_task=None
        self.list_status={'state':'not started','sample_seconds':1,'checkpoint_seconds':30}
        self.continuations=None

    def record(self, kind, payload, observed_at=None):
        now=time.time() if observed_at is None else observed_at
        self.sequence+=1
        size=len(json.dumps(payload,allow_nan=False).encode('utf-8'))
        if len(self.pending)>=self.capacity or self.pending_bytes+size>self.byte_capacity:
            self.dropped+=1
            log.error('Scanner journal queue full: %s evidence rows lost',self.dropped)
            return
        self.pending.append({'id':f'{self.run_id}:{self.sequence:012d}',
            'run_id':self.run_id,'kind':kind,'observed_at':now,
            'session_date':datetime.fromtimestamp(now,ET).date().isoformat(),
            'payload':copy.deepcopy(payload),'_bytes':size})
        self.pending_bytes+=size

    def status(self):
        fresh=self.last_saved_at is not None and time.time()-self.last_saved_at<90
        return {'state':'unavailable' if self.error else 'saving' if fresh else 'waiting for durable write',
                'run_id':self.run_id,'pending':len(self.pending),'saved':self.saved,
                'pending_payload_bytes':self.pending_bytes,'payload_byte_limit':self.byte_capacity,
                'dropped':self.dropped,'last_saved_at':self.last_saved_at,'error':self.error,
                'write_failures':self.failures,'last_failure_at':self.last_failure_at,
                'lists':dict(self.list_status),
                'continuation':dict(self.continuations.status) if self.continuations else {'state':'not started'},
                'payload_storage':CODEC,
                'rules_version':RULES,'revision':os.getenv('RAILWAY_GIT_COMMIT_SHA','unknown')}

    async def flush(self):
        batch=list(self.pending)[:500]
        if not batch:return
        try:
            await asyncio.to_thread(self.store.write,batch)
        except Exception as exc:
            # SQL/provider errors could contain secrets. Preserve the cause
            # while redacting credentials before logging or rendering it.
            detail=f'{type(exc).__name__}: {exc}'
            for name,value in os.environ.items():
                if value and any(s in name for s in ('PASSWORD','TOKEN','KEY')):
                    detail=detail.replace(value,'[REDACTED]')
            self.error=detail;self.failures+=1;self.last_failure_at=time.time()
            log.error('Scanner journal write failed: %s',detail)
            return
        for _ in batch:self.pending_bytes-=self.pending.popleft()['_bytes']
        self.saved+=len(batch);self.last_saved_at=time.time();self.error=None

    async def sample_lists(self, tape):
        from analysis.scanner_list_history import ListHistory
        history=ListHistory(self.record)
        while not self.stopping:
            began=time.perf_counter()
            now=time.time()
            try:
                if self.continuations:self.continuations.sample(tape.book,now)
                snapshot=tape.book.scanner_snapshot(now)
                history.capture(snapshot,now,tape.book.window_generation,tape.state)
                self.list_status.update(state='sampling',last_sample_at=now,error=None,
                    duration_ms=(time.perf_counter()-began)*1000)
            except Exception as exc:
                # No credential-bearing I/O in snapshotting. Keep the failure
                # visible and continue; never silently turn it into an empty list.
                detail=f'{type(exc).__name__}: {exc}'
                self.list_status.update(state='unavailable',error=detail)
                self.record('list_error',{'error':detail},now)
                log.exception('Scanner list sampling failed')
            await asyncio.sleep(max(.05,1-(time.perf_counter()-began)))

    async def run(self, tape):
        from analysis.scanner_continuation import Continuations
        self.continuations=Continuations(self.record)
        tape.book.continuations=self.continuations
        self.continuations.task=asyncio.create_task(self.continuations.refresh())
        self.record('run_start',{'rules_version':RULES,'revision':os.getenv('RAILWAY_GIT_COMMIT_SHA','unknown'),
            'payload_storage':CODEC,'storage_note':'Lossless independent chunks; unchanged events and sampling; history exports restore original payloads.',
            'scope':'Move alerts and leaders/fresh/focus/volume/continuation lists in all price bands; recent-runner universe and volume/momentum return states. No browser filters. Not exact Warrior eligibility.',
            'heartbeat_seconds':30,'flush_seconds':1,'warmup_seconds':600})
        self.list_task=asyncio.create_task(self.sample_lists(tape))
        last=0
        while not self.stopping:
            now=time.time()
            if now-last>=30:
                self.record('heartbeat',{'stream_state':tape.state,'reason':tape.reason,
                    'market_at':tape.book.last_event_at,'received_at':tape.book.last_message_at,
                    'warm_since':tape.book.warm_since,'alert_total':tape.book.alert_total,
                    'tracked_count':len(tape.book.rows),'journal':self.status()},now)
                last=now
            await self.flush()
            await asyncio.sleep(1 if not self.error else 5)
        await self.list_task
        self.continuations.stopping=True
        self.continuations.task.cancel()
        try:await self.continuations.task
        except asyncio.CancelledError:pass
        self.record('run_stop',{'journal':self.status(),'stream_state':tape.state})
        while self.pending:
            await self.flush()
            if self.error:break


class TapeLifespan:
    """Start the existing singleton on ASGI startup, not on a browser request."""
    def __init__(self, app, tape):self.app=app;self.tape=tape
    async def __call__(self, scope, receive, send):
        if scope['type']!='lifespan':
            return await self.app(scope,receive,send)
        async def lifecycle_send(message):
            if message['type']=='lifespan.startup.complete':
                await self.tape.ensure_started()
            elif message['type']=='lifespan.shutdown.complete':
                await self.tape.stop()
            await send(message)
        await self.app(scope,receive,lifecycle_send)
