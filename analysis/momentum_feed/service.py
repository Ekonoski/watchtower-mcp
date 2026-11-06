"""Private persisted model ledger, scheduler and narrow read-only chart endpoint."""
import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from psycopg2.extras import Json
from starlette.responses import JSONResponse
from screen.market_calendar import is_trading_day
from screen.reversal_screen import _conn
from .model import payload, seed, session_after

log = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')


class ReconciliationRequired(RuntimeError):
    """A known missing observation that scheduled retries cannot recreate."""


SCHEMA = '''
CREATE SCHEMA IF NOT EXISTS wt_momentum;
REVOKE ALL ON SCHEMA wt_momentum FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS wt_momentum.settings (
 id integer PRIMARY KEY CHECK (id=1), enabled boolean NOT NULL DEFAULT false,
 token_hash text NOT NULL, last_checked timestamptz, error text,
 anchor jsonb, payload jsonb);
CREATE TABLE IF NOT EXISTS wt_momentum.observations (
 day date NOT NULL, phase text NOT NULL CHECK(phase IN ('open','close')),
 observed_at timestamptz NOT NULL, data jsonb NOT NULL,
 PRIMARY KEY(day,phase));
CREATE TABLE IF NOT EXISTS wt_momentum.snapshots (
 day date NOT NULL, phase text NOT NULL, generated_at timestamptz NOT NULL DEFAULT now(),
 payload jsonb NOT NULL, PRIMARY KEY(day,phase));
ALTER TABLE wt_momentum.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE wt_momentum.observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE wt_momentum.snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA wt_momentum FROM PUBLIC, anon, authenticated;
'''


def expected(now):
    day = now.date()
    if is_trading_day(day) and now.time().replace(tzinfo=None)>=time(16,20):
        return day.isoformat(),'close'
    if is_trading_day(day) and now.time().replace(tzinfo=None)>=time(9,31):
        return day.isoformat(),'open'
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day.isoformat(),'close'


def status(p, checked, error, now):
    target, phase = expected(now)
    complete = p and p['asof']==target and (p['phase']==phase or p['phase']=='close')
    heartbeat = checked and 0 <= (now-checked).total_seconds() <= 420
    return 'ready' if complete and heartbeat and not error else 'unavailable'


def run():
    """Idempotent, database-locked, once per two minutes; zero brokerage I/O."""
    conn = None
    previous_error = None
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(20261031, 1075)')
            if not cur.fetchone()[0]:
                return
            cur.execute('SELECT enabled,anchor,payload,error FROM wt_momentum.settings WHERE id=1')
            row = cur.fetchone()
            if not row or not row[0]:
                return
            _,anchor,current,previous_error = row
            from .provider import check_seed_anchor, observe
            if not anchor:
                anchor = check_seed_anchor()
            cur.execute("SELECT data FROM wt_momentum.observations ORDER BY day, CASE phase WHEN 'open' THEN 0 ELSE 1 END")
            observations = [v[0] for v in cur.fetchall()]
            now = datetime.now(ET)
            today = now.date().isoformat()
            clock = now.time().replace(tzinfo=None)
            initial = current is None
            current = current or payload(observations)
            ob = None
            required = session_after(current['asof']) if current['phase'] == 'close' else current['asof']
            if required < today or (required == today and current['phase'] == 'close' and clock > time(9,45)):
                raise ReconciliationRequired('Reconciliation required for '+required+
                                   (' open' if current['phase'] == 'close' else ' close')+
                                   '; last recorded payload '+current['asof']+' '+current['phase']+
                                   '. No retrospective fills will be labeled live.')
            if is_trading_day(now) and time(9,31)<=clock<=time(9,45) and current['asof']<today:
                if current['phase']!='close' or session_after(current['asof'])!=today:
                    raise RuntimeError('Missing prior completed session; reconciliation required')
                ob = observe(today,'open')
            elif is_trading_day(now) and clock>=time(16,20) and (current['asof']!=today or current['phase']!='close'):
                opening = next((x for x in observations if x['day']==today and x['phase']=='open'),None)
                if not opening:
                    raise RuntimeError('Missing recorded opening observation; no fabricated retrospective fills')
                ob = observe(today,'close',opening)
            if ob:
                observations.append(ob)
                current = payload(observations)
                cur.execute('INSERT INTO wt_momentum.observations(day,phase,observed_at,data) VALUES(%s,%s,%s,%s)',
                            (ob['day'],ob['phase'],ob['observed_at'],Json(ob)))
            cur.execute('INSERT INTO wt_momentum.snapshots(day,phase,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                        (current['asof'],current['phase'],Json(current)))
            target, phase = expected(now)
            error = None if current['asof']==target and (current['phase']==phase or current['phase']=='close') else 'Waiting for required '+target+' '+phase+' observation'
            if initial or ob:
                cur.execute('UPDATE wt_momentum.settings SET anchor=%s,payload=%s,last_checked=now(),error=%s WHERE id=1',
                            (Json(anchor),Json(current),error))
            else:
                cur.execute('UPDATE wt_momentum.settings SET last_checked=now(),error=%s WHERE id=1',(error,))
        conn.commit()
    except Exception as exc:
        # A persistent data hole is still unavailable, but is not a new incident
        # every two minutes. A changed cause still gets its complete traceback.
        if not isinstance(exc, ReconciliationRequired) or str(exc) != previous_error:
            log.exception('[momentum_feed] refresh unavailable')
        if conn:
            conn.rollback()
            try:
                with conn.cursor() as cur:
                    cur.execute('UPDATE wt_momentum.settings SET last_checked=now(),error=%s WHERE id=1',(str(exc),))
                conn.commit()
            except Exception:
                conn.rollback()
                log.exception('[momentum_feed] could not persist refresh failure')
    finally:
        if conn:
            conn.close()  # also releases session advisory lock


def read_feed(token):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT token_hash,enabled,last_checked,error,payload,anchor FROM wt_momentum.settings WHERE id=1')
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),row[0]):
        return {'error':'Unauthorized'},401
    _,enabled,checked,error,p,anchor = row
    if not enabled:
        return {'error':'Feed disabled'},503
    now = datetime.now(ET)
    state = status(p,checked,error,now)
    return {**(p or {}),'status':state,'error':error,'server_time':now.isoformat(),
            'checked_at':checked.isoformat() if checked else None,
            'expires_at':(now+timedelta(minutes=3)).isoformat(), 'anchor_verified':bool(anchor),
            'plans':p.get('plans',[]) if p and state=='ready' else []},200


def register_routes(mcp):
    @mcp.custom_route('/integrations/trendspider/momentum-v1', methods=['GET'])
    async def feed(request):
        auth = request.headers.get('authorization','')
        token = auth[7:] if auth.startswith('Bearer ') else ''
        if not token:
            return JSONResponse({'error':'Unauthorized'},status_code=401)
        try:
            body, code = await asyncio.to_thread(read_feed,token)
        except Exception:
            log.exception('[momentum_feed] read unavailable')
            body,code = {'error':'Portfolio feed unavailable'},503
        return JSONResponse(body,status_code=code,headers={'Cache-Control':'no-store'})
