"""Preview/apply an explicitly labeled model reconstruction after a data gap.

Never called by the scheduler. No broker or paper-desk writes. Apply uses the
exact preview, refuses changed state, and commits all rows atomically.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from psycopg2.extras import Json
from analysis.momentum_feed.model import payload, session_after
from analysis.momentum_feed.provider import reconstruct
from analysis.momentum_feed.service import _conn, expected, ET
from datetime import datetime


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load(cur):
    cur.execute('SELECT enabled,payload FROM wt_momentum.settings WHERE id=1')
    enabled, current = cur.fetchone()
    if not enabled:
        raise ValueError('Feed is disabled; reconciliation cannot activate it')
    cur.execute("SELECT data FROM wt_momentum.observations ORDER BY day, CASE phase WHEN 'open' THEN 0 ELSE 1 END")
    observations = [r[0] for r in cur.fetchall()]
    return current, observations


def preview(path):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            current, observations = load(cur)
        base_hash = fingerprint([current, observations])
        target, target_phase = expected(datetime.now(ET))
        additions = []
        while (current['asof'], current['phase']) != (target, target_phase):
            if current['phase'] == 'close':
                day, phase, opening = session_after(current['asof']), 'open', None
            else:
                day, phase = current['asof'], 'close'
                opening = next(x for x in observations if x['day'] == day and x['phase'] == 'open')
            if day > target:
                raise ValueError('Target precedes current record')
            ob = reconstruct(day, phase, opening)
            observations.append(ob)
            current = payload(observations)
            additions.append({'observation':ob, 'payload':current})
        plan = {'base_hash':base_hash, 'generated_at':datetime.now(ET).isoformat(),
                'target':[target,target_phase], 'additions':additions}
        path.write_text(json.dumps(plan), encoding='utf-8')
        summary(plan)
    finally:
        conn.close()


def summary(plan):
    print(json.dumps({'target':plan['target'], 'observations':[
        {'day':x['observation']['day'],'phase':x['observation']['phase'],
         'source':x['observation']['source'], 'symbols':len(x['observation']['bars'])}
        for x in plan['additions']], 'events':[
            {'symbol':s,**e} for s, es in plan['additions'][-1]['payload']['events'].items()
            for e in es if e['date'] in {x['observation']['day'] for x in plan['additions']}]
        if plan['additions'] else []}, indent=2))


def apply(path):
    plan = json.loads(path.read_text(encoding='utf-8'))
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(20261031, 1075)')
            if not cur.fetchone()[0]:
                raise ValueError('Feed refresh is running; try again after it completes')
            current, observations = load(cur)
            if fingerprint([current, observations]) != plan['base_hash']:
                raise ValueError('Feed record changed since preview; generate a new preview')
            for item in plan['additions']:
                ob = item['observation']
                if ob.get('source') != 'reconciled_model':
                    raise ValueError('Recovery must be labeled reconciled_model')
                observations.append(ob)
                current = payload(observations)
                if current != item['payload']:
                    raise ValueError('Preview does not match deterministic replay')
                cur.execute('INSERT INTO wt_momentum.observations(day,phase,observed_at,data) VALUES(%s,%s,%s,%s)',
                            (ob['day'],ob['phase'],ob['observed_at'],Json(ob)))
                cur.execute('INSERT INTO wt_momentum.snapshots(day,phase,payload) VALUES(%s,%s,%s)',
                            (current['asof'],current['phase'],Json(current)))
            target, phase = expected(datetime.now(ET))
            error = None if (current['asof'],current['phase']) == (target,phase) else 'Reconciliation applied; waiting for '+target+' '+phase
            cur.execute('UPDATE wt_momentum.settings SET payload=%s,last_checked=now(),error=%s WHERE id=1',
                        (Json(current),error))
        conn.commit()
        summary(plan)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview',type=Path)
    mode.add_argument('--apply',type=Path)
    args = parser.parse_args()
    preview(args.preview) if args.preview else apply(args.apply)
