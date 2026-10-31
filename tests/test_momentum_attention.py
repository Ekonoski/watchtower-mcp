from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import pytest
from analysis.momentum_attention import classify, expected_close

def data(tail=None):
    days=pd.bdate_range(end='2026-10-30',periods=206).strftime('%Y-%m-%d')
    rows=[]
    for s in ['SPY','QQQ','TEST']:
        c=np.full(206,100.)
        if s=='TEST':c[-len(tail):]=tail
        rows.extend((s,d,float(p),1_000_000.) for d,p in zip(days,c))
    return pd.DataFrame(rows,columns=['ticker','day','close','volume'])

def test_first_green_near_zero():
    r=classify(data([101.]),{'TEST':'Example'},'2026-10-30')['rows'][0]
    assert r['green_age']==0 and r['rising'] and r['near_zero']
    assert r['momentum_pct']==pytest.approx(1)

def test_recent_green_falling_is_visible_and_warned():
    r=classify(data([102.,101.]),{'TEST':'Example'},'2026-10-30')['rows'][0]
    assert r['green_age']==1 and not r['rising']
    assert 'Momentum falling today' in r['warnings']

def test_established_falling_removed_and_rising_admitted():
    assert not classify(data([102.]*6+[101.]),{'TEST':'Example'},'2026-10-30')['rows']
    r=classify(data([101.]*6+[102.]),{'TEST':'Example'},'2026-10-30')['rows'][0]
    assert r['group']=='green_rising'

def test_lost_green_removed_even_if_recent():
    assert not classify(data([102.,99.]),{'TEST':'Example'},'2026-10-30')['rows']

def test_missing_stock_is_counted_not_failed_eligibility():
    d=data([101.]);d=d[~((d.ticker=='TEST')&(d.day=='2026-10-30'))]
    r=classify(d,{'TEST':'Example'},'2026-10-30')
    assert r['coverage']['unavailable']==1 and r['excluded'][0]['reason']=='stale daily bar'

def test_missing_benchmark_fails_whole_read():
    with pytest.raises(ValueError,match='QQQ'):
        classify(data([101.]).query("ticker!='QQQ'"),{'TEST':'Example'},'2026-10-30')

def test_intraday_future_bar_ignored():
    d=data([101.]);extra=pd.DataFrame([('TEST','2026-11-02',10000.,1e8)],columns=d.columns)
    assert classify(pd.concat([d,extra]),{'TEST':'Example'},'2026-10-30')['rows'][0]['close']==101

def test_completion_calendar():
    et=ZoneInfo('America/New_York')
    assert expected_close(datetime(2026,10,31,12,tzinfo=et))=='2026-10-30'
    assert expected_close(datetime(2026,11,2,15,tzinfo=et))=='2026-10-30'
    assert expected_close(datetime(2026,11,2,16,35,tzinfo=et))=='2026-11-02'
