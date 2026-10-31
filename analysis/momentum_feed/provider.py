"""Massive observations. Opening prints are captured, never invented or backfilled."""
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import requests
from .model import ALIASES, LIVE, seed

ET = ZoneInfo('America/New_York')
BASE = 'https://api.massive.com'


def get(path, params=None):
    key = os.environ.get('POLYGON_API_KEY')
    if not key:
        raise RuntimeError('Massive API credential unavailable')
    url = path if path.startswith('https://') else BASE+path
    if urlparse(url).hostname not in ('api.massive.com', 'api.polygon.io'):
        raise ValueError('Unexpected Massive pagination host')
    r = requests.get(url, params=params, headers={'Authorization':'Bearer '+key}, timeout=25)
    if r.status_code != 200:
        # No credential-bearing URLs or request headers in errors.
        raise RuntimeError('Massive HTTP '+str(r.status_code)+': '+r.text[:2000])
    body = r.json()
    if body.get('status') in ('ERROR','NOT_AUTHORIZED'):
        raise RuntimeError('Massive rejected data request: '+str(body.get('error', body.get('message'))))
    return body


def rows(path, params):
    out = []
    while path:
        b = get(path, params)
        out.extend(b.get('results', []))
        path, params = b.get('next_url'), None
    return out


def grouped(day):
    b = get('/v2/aggs/grouped/locale/us/market/stocks/'+day, {'adjusted':'false'})
    if not b.get('results'):
        raise RuntimeError('No completed daily bars for '+day)
    return {v['T']:v for v in b['results']}


def validate_bar(b, symbol):
    if not all(math.isfinite(b[k]) for k in ('o','h','l','c','v')):
        raise ValueError('Nonfinite bar: '+symbol)
    if not (0 < b['l'] <= min(b['o'],b['c']) <= max(b['o'],b['c']) <= b['h']) or b['v'] < 0:
        raise ValueError('Invalid OHLCV: '+symbol)


def observe(day, phase, opening=None):
    now = datetime.now(ET)
    if day != now.date().isoformat():
        raise ValueError('Forward observations cannot backfill a missed day')
    if phase == 'open' and not (time(9,31) <= now.time().replace(tzinfo=None) <= time(9,45)):
        raise ValueError('Opening capture window missed; manual reconciliation required')
    if phase == 'close' and (now.hour*60+now.minute < 16*60+20 or opening is None):
        raise ValueError('Close requires settled daily data and a recorded opening observation')
    daily = grouped(day) if phase == 'close' else None
    def one(s):
        ticker = ALIASES.get(s,s)
        if opening:
            factor = opening['factors'][s]
            dividends = [x for x in opening['dividends'] if x['symbol']==s]
        else:
            splits = rows('/stocks/v1/splits', {'ticker':ticker, 'execution_date.gt':seed()['cutoff'], 'execution_date.lte':day, 'limit':5000})
            factor = 1.
            for x in splits:
                ratio = float(x['split_to'])/float(x['split_from'])
                if not math.isfinite(ratio) or ratio <= 0:
                    raise ValueError('Invalid split ratio: '+s)
                factor *= ratio
            dividends = []
            for x in rows('/stocks/v1/dividends', {'ticker':ticker, 'ex_dividend_date':day, 'limit':5000}):
                if x.get('currency')!='USD' or x.get('cash_amount') is None or not x.get('pay_date') or x['pay_date']<day:
                    raise ValueError('Unresolved distribution: '+s+' '+str(x))
                dividends.append({'symbol':s,'id':x['id'],'pay_date':x['pay_date'],
                                  'amount_per_unit':float(x['cash_amount'])*factor})
        if phase == 'open':
            start = int(datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ET).timestamp()*1000)
            items = rows('/v2/aggs/ticker/'+ticker+'/range/1/minute/'+str(start)+'/'+str(start+59999),
                         {'adjusted':'false','sort':'asc','limit':10})
            if not items or items[0]['t'] != start:
                raise ValueError('Missing regular-session opening minute: '+s)
            raw = items[0]
            b = {k:float(raw['o']) for k in ('o','h','l','c')}
            b['v'] = 0.
        else:
            raw = daily.get(ticker)
            if not raw:
                raise ValueError('Missing completed daily bar: '+s)
            b = {k:float(raw[k]) for k in ('o','h','l','c','v')}
        raw_bar = dict(b)
        for k in ('o','h','l','c'):
            b[k] *= factor
        b['v'] /= factor
        validate_bar(b,s)
        if opening:
            # The captured print is the fill authority; later revisions must not
            # silently rewrite the opening model's entries or position sizes.
            b['o'] = opening['bars'][s]['o']
            b['h'] = max(b['h'],b['o'])
            b['l'] = min(b['l'],b['o'])
        return s,b,factor,dividends,raw_bar
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one,LIVE))
    stamp = datetime.now(ET).isoformat()
    return {'day':day,'phase':phase,'observed_at':stamp,
            'open_observed_at':opening['observed_at'] if opening else stamp,
            'bars':{s:b for s,b,_,_,_ in results},
            'raw_bars':{s:b for s,_,_,_,b in results},
            'factors':{s:f for s,_,f,_,_ in results},
            'dividends':[d for _,_,_,ds,_ in results for d in ds]}


def check_seed_anchor():
    """Verify the installed research cutoff against the real provider once."""
    base = seed()
    daily = grouped(base['cutoff'])
    for s in LIVE:
        bar = daily.get(ALIASES.get(s,s))
        if not bar:
            raise ValueError('Seed anchor missing current ticker: '+s)
        expected = base['data'][s]['c'][-1]
        if not math.isclose(float(bar['c']),expected,rel_tol=1e-5,abs_tol=.001):
            raise ValueError('Seed/provider closing-price mismatch: '+s)
    # Check permissions for the two forward corporate-action endpoints too.
    rows('/stocks/v1/splits', {'ticker':'AAPL','execution_date.gt':base['cutoff'],'limit':5000})
    rows('/stocks/v1/dividends', {'ticker':'AAPL','ex_dividend_date':base['cutoff'],'limit':5000})
    return {'session':base['cutoff'],'symbols':len(LIVE),'verified_at':datetime.now(ET).isoformat(),
            'corporate_action_endpoints':'verified'}
