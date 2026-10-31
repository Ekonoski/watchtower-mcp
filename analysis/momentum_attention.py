"""Daily review list. No portfolio orders, trading signals, or watchlist mutations."""
from collections import Counter
from datetime import datetime, timedelta, time
from functools import lru_cache
import logging
import threading
import time as clock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo('America/New_York')
log = logging.getLogger(__name__)
_lock = threading.Lock()
_cached = None
_cached_at = 0

def expected_close(now):
    from screen.market_calendar import is_trading_day
    day = now.astimezone(ET).date()
    if now.astimezone(ET).time().replace(tzinfo=None) < time(16,35):
        day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day.isoformat()

@lru_cache(maxsize=2)
def universe(day):
    from analysis.polygon_data import get_client
    client = get_client()
    if client is None:
        raise RuntimeError('Massive stock reference unavailable: no configured client')
    out = {}
    # Full paginated reference list, including common-share ADRs, excluding funds,
    # warrants/preferreds and OTC. Refresh the classification every calendar day.
    for typ in ('CS','ADRC'):
        for t in client.list_tickers(market='stocks', active=True, type=typ, limit=1000):
            if t.locale == 'us' and t.primary_exchange in ('XNAS','XNYS','XASE','BATS','ARCX'):
                out[t.ticker] = t.name
    if not out:
        raise RuntimeError('Massive stock reference returned zero listed common stocks')
    return out

def classify(frame, names, expected):
    """Exact session alignment; unavailable history never becomes a failed filter."""
    frame = frame.copy()
    frame['day'] = frame['day'].astype(str)
    frame = frame[frame.day <= expected]
    grouped = {s:a.sort_values('day').set_index('day') for s,a in frame.groupby('ticker')}
    for sym in ('SPY','QQQ'):
        if sym not in grouped or grouped[sym].index[-1] != expected:
            raise ValueError(sym+' completed daily data unavailable for '+expected)
    cal = grouped['SPY'].index[-206:]
    if len(cal) < 206:
        raise ValueError('Insufficient SPY calendar history')
    scores = {}
    for sym in ('SPY','QQQ'):
        c = grouped[sym].reindex(cal)['close'].astype(float)
        if c.isna().any() or (c<=0).any():
            raise ValueError(sym+' benchmark has missing/nonpositive history')
        scores[sym] = 50*(c/c.shift(63)+c/c.shift(126)-2)
    rows=[];excluded=[];liquid=0;evaluable=0
    for sym,name in sorted(names.items()):
        a = grouped.get(sym)
        if a is None:
            excluded.append({'ticker':sym,'reason':'no stored daily history','as_of':None});continue
        last = str(a.index[-1])
        if last != expected:
            excluded.append({'ticker':sym,'reason':'stale daily bar','as_of':last});continue
        a = a.reindex(cal)
        c = a['close'].astype(float);v=a['volume'].astype(float)
        if c.isna().any() or v.isna().any() or (c<=0).any() or (v<0).any():
            excluded.append({'ticker':sym,'reason':'missing/invalid 206-session history (includes new listings)','as_of':last});continue
        evaluable+=1
        dollar=(c*v).rolling(20).mean()
        if dollar.iloc[-1] < 20_000_000:continue
        liquid+=1
        score=50*(c/c.shift(63)+c/c.shift(126)-2)
        sma=c.rolling(200).mean()
        green=(c>sma)&(dollar>=20_000_000)&(score>scores['SPY'])&(score>scores['QQQ'])
        # Only 6 fully ready states are available; prior ready bar is essential.
        recent=[j for j in range(len(cal)-5,len(cal)) if green.iloc[j] and not green.iloc[j-1]]
        age=len(cal)-1-recent[-1] if recent else None
        rise=bool(score.iloc[-1]>score.iloc[-2])
        if not green.iloc[-1] or not (rise or age is not None):continue
        ema20=c.ewm(span=20,adjust=False).mean().iloc[-1]
        delta=float(score.iloc[-1]-score.iloc[-2])
        value=float(score.iloc[-1]);distance=float((c.iloc[-1]/ema20-1)*100)
        warnings=[]
        if not rise:warnings.append('Momentum falling today')
        if distance>10:warnings.append('More than 10% above EMA20')
        if c.pct_change(fill_method=None).abs().max()>.40:
            warnings.append('Large historical price discontinuity: verify chart/corporate action')
        rows.append({'ticker':sym,'company':name,'as_of':last,'close':float(c.iloc[-1]),
                     'group':'new_green' if age is not None else 'green_rising',
                     'reason':('Turned green '+('today' if age==0 else str(age)+' sessions ago')) if age is not None else 'Green with momentum rising',
                     'green_age':age,'momentum_pct':value,'momentum_change_pp':delta,
                     'rising':rise,'near_zero':abs(value)<=5,'dollar_volume_m':float(dollar.iloc[-1]/1e6),
                     'ema20_distance_pct':distance,'warnings':warnings,'earnings':None})
    rows.sort(key=lambda r:(r['group']!='new_green',abs(r['momentum_pct']),r['ticker']))
    return {'version':'attention_v1','as_of':expected,'rows':rows,'count':len(rows),
            'counts':dict(Counter(r['group'] for r in rows)),
            'coverage':{'reference_stocks':len(names),'evaluable':evaluable,'liquid':liquid,
                        'unavailable':len(excluded),'unavailable_reasons':dict(Counter(r['reason'] for r in excluded))},
            'excluded':excluded,'benchmark':{s:float(v.iloc[-1]) for s,v in scores.items()},
            'note':'Review candidates, not entry signals. New green within 5 completed sessions OR green and momentum rising; average daily dollar volume >= $20M. Near zero means +/-5 percentage points and is a review tag, not a proven edge. Common stocks and common-share ADRs on US exchanges; coverage gaps listed explicitly.'}

def read_attention():
    global _cached,_cached_at
    now=datetime.now(ET);target=expected_close(now)
    with _lock:
        if _cached and clock.monotonic()-_cached_at<120 and _cached['as_of']==target:
            return _cached
        names=universe(now.date().isoformat())
        from screen.reversal_screen import _conn
        conn=_conn()
        try:
            with conn.cursor() as cur:
                # PK(ticker,trade_date) supports each bounded lateral lookup.
                cur.execute('''SELECT u.ticker,p.trade_date::text,p.close,p.volume
                    FROM unnest(%s::text[]) AS u(ticker) CROSS JOIN LATERAL
                    (SELECT trade_date,close,volume FROM daily_prices
                     WHERE ticker=u.ticker AND trade_date<=%s
                     ORDER BY trade_date DESC LIMIT 206) p''', (list(names)+['SPY','QQQ'],target))
                raw=cur.fetchall()
                result=classify(pd.DataFrame(raw,columns=['ticker','day','close','volume']),names,target)
                cur.execute('''SELECT DISTINCT ON(ticker) ticker,report_date::text,time_of_day,confirmed
                    FROM earnings_calendar WHERE ticker=ANY(%s) AND report_date>=%s
                    ORDER BY ticker,report_date''',([r['ticker'] for r in result['rows']],now.date()))
                earnings={r[0]:{'date':r[1],'time':r[2],'confirmed':r[3]} for r in cur.fetchall()}
                for r in result['rows']:r['earnings']=earnings.get(r['ticker'])
        finally:conn.close()
        result['generated_at']=now.isoformat()
        _cached=result;_cached_at=clock.monotonic()
        return result
