"""Deterministic forward replay and dated chart payload; no broker access."""
import copy
import gzip
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
import numpy as np
import pandas as pd
from screen.market_calendar import is_trading_day
from . import engine
from .universe import OLD, CONFIG

ALIASES = {'FB': 'META', 'PCLN': 'BKNG', 'SQ': 'XYZ'}
TERMINATED = {'AABA', 'AET'}
LIVE = [s for s in OLD['universe'] if s not in TERMINATED] + ['SPY', 'QQQ']


@lru_cache(maxsize=1)
def seed():
    with gzip.open(Path(__file__).with_name('seed.json.gz'), 'rt') as f:
        return json.load(f)


def session_after(day):
    d = date.fromisoformat(day) + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d.isoformat()


def block(day):
    d = date.fromisoformat(day)
    y, w, _ = d.isocalendar()
    return y, w, int(d.weekday() >= 3)


def replay(observations):
    base = seed()
    cal = list(base['calendar'])
    data = {s: {k: np.array(v, dtype=float) for k, v in a.items()} for s, a in base['data'].items()}
    divs = copy.deepcopy(base['dividends'])
    latest = {}
    # The persisted close supersedes only the same session's provisional bar.
    for ob in observations:
        latest[ob['day']] = ob
    for day, ob in sorted(latest.items()):
        if day != session_after(cal[-1]):
            raise ValueError('Missing completed session before '+day)
        if set(ob['bars']) != set(LIVE):
            raise ValueError('Incomplete cohort bars for '+day)
        cal.append(day)
        for s, a in data.items():
            b = ob['bars'].get(s)
            for k in ('o', 'h', 'l', 'c', 'v'):
                a[k] = np.append(a[k], b[k] if b else np.nan)
            c = a['c']
            for n in (63, 126):
                a['ret'+str(n)] = np.append(a['ret'+str(n)], c[-1]/c[-1-n]-1)
            a['sma200'] = np.append(a['sma200'], np.mean(c[-200:]))
            if 'dv20' in a:
                a['dv20'] = np.append(a['dv20'], np.mean(c[-20:]*a['v'][-20:]))
            if 'atr' in a:
                tr = max(a['h'][-1]-a['l'][-1], abs(a['h'][-1]-c[-2]), abs(a['l'][-1]-c[-2]))
                a['atr'] = np.append(a['atr'], (13*a['atr'][-1]+tr)/14)
        # Entire day's cash distributions, checked before its opening decision.
        divs[day] = ob['dividends']
    phase = latest[cal[-1]]['phase'] if latest else 'close'
    result = engine.simulate(cal, data, divs, CONFIG, [base['start'], cal[-1]],
                             cost=base['cost'], corporate_exits=base['corporate_exits'],
                             liquidate=False, open_only=phase == 'open')
    return cal, data, result, phase, latest


def payload(observations):
    cal, data, r, phase, latest = replay(observations)
    asof = cal[-1]
    signal_i = -2 if phase == 'open' else -1
    signal_day = cal[signal_i]
    next_day = session_after(asof) if phase == 'close' else None
    chosen = engine.targets(data, signal_i, CONFIG)
    retained = engine.targets(data, signal_i, {**CONFIG, 'rank_limit': 15})
    events = {}

    def factor(s, day):
        return latest.get(day, {}).get('factors', {}).get(s, 1.)

    def event(s, day, side, px, reason):
        ob = latest.get(day)
        events.setdefault(s, []).append({
            'date': day, 'side': side, 'price': px/factor(s, day), 'reason': reason,
            'source': ob.get('source', 'forward_model') if ob else 'research_replay',
            'observed_at': ob.get('open_observed_at', ob['observed_at']) if ob else None})

    for t in r['trades']:
        event(t['symbol'], t['entry_date'], 'BUY', t['entry_price'], 'rank_entry')
        event(t['symbol'], t['exit_date'], 'SETTLE' if t['reason']=='corporate_consideration' else 'SELL', t['exit_price'], t['reason'])
    for s, p in r['active'].items():
        event(s, p['entry_date'], 'BUY', p['entry_price'], 'rank_entry')
    for leg in r['core_legs']:
        event('QQQ', leg['date'], leg['side'].upper(), leg['price'], leg['reason'])
    for es in events.values():
        es.sort(key=lambda e: (e['date'], e['side']=='BUY'))
    held = {s: {**p, 'stop': p['stop']/factor(s, asof), 'units': p['units']*factor(s, asof),
                'entry_price': p['entry_price']/factor(s, p['entry_date'])} for s, p in r['active'].items()}
    held['QQQ'] = {'units': r['core_units']*factor('QQQ', asof), 'stop': None, 'role':'reserve'}
    plans = []
    if next_day:
        rebalance = block(next_day) != block(asof)
        kept = dict(r['active'])
        for s, p in r['active'].items():
            why = p.get('exit_reason') or ('rank_exit' if rebalance and s not in retained else None)
            if why:
                plans.append({'symbol':s,'side':'SELL','reason':why,'session':next_day})
                kept.pop(s)
        if rebalance:
            # Conditional priority list: gaps can cause an earlier candidate to
            # be skipped and a later one to fill. Never label this as a fill.
            slots = 10-len(kept)
            for s in chosen:
                if s in kept or slots <= 0:
                    continue
                sector = OLD['sectors'][s]
                if sum(p['sector']==sector for p in kept.values()) >= 4:
                    continue
                a = data[s]
                plans.append({'symbol':s,'side':'BUY','reason':'conditional_rank_entry',
                              'session':next_day,'priority':len(plans)+1,'available_slots':slots,
                              'maximum_open':float(a['c'][-1]+a['atr'][-1])/factor(s,asof),
                              'risk_per_share':float(3*a['atr'][-1])/factor(s,asof)})
    reconciled = sorted(day for day, ob in latest.items() if ob.get('source') == 'reconciled_model')
    return {'version':'retention_buffer_v1','asof':asof,'phase':phase,'signal_close':signal_day,
            'reconciled_sessions':reconciled,
            'seed_cutoff':seed()['cutoff'],'next_session':next_day,'events':events,'held':held,
            'plans':plans,'top10':chosen,'retain15':retained,'universe':OLD['universe']+['QQQ'],
            'aliases':ALIASES,'cost_per_side':.0015,'account':r['curve'][-1],
            'note':'Model signals, not brokerage fills. Research history through 2026-10-30. Conditional plans need opening price checks.' +
                   (' Historical incident reconstruction (not live observations): '+', '.join(reconciled)+'.' if reconciled else ''),
            'retired_symbols':sorted(TERMINATED)}
