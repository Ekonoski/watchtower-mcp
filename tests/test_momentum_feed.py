import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import pytest
from analysis.momentum_feed.model import seed, payload, replay, session_after, LIVE
from analysis.momentum_feed.service import status, expected
from analysis.momentum_feed.provider import observe, validate_bar

ET = ZoneInfo('America/New_York')

@pytest.fixture(scope='module')
def initial():
    return payload([])

def opening():
    s=seed()
    return {'day':'2026-11-02','phase':'open','observed_at':'2026-11-02T09:32:00-05:00',
            'factors':{k:1. for k in LIVE},'dividends':[],
            'bars':{k:{**{f:s['data'][k]['c'][-1] for f in ('o','h','l','c')},'v':0.} for k in LIVE}}

def test_bootstrap_retains_audited_book_not_forced_liquidation(initial):
    assert set(initial['held'])=={'MU','MSFT','MRK','V','CVX','XOM','AAPL','IBM','DIS','TSLA','QQQ'}
    assert initial['plans']==[]
    assert initial['next_session']=='2026-11-02'
    assert 'JNJ' in initial['top10'] and 'JNJ' not in initial['held']
    assert initial['held']['AAPL']['stop']==pytest.approx(320.6801045)
    assert all(e['reason']!='period_end' for es in initial['events'].values() for e in es)
    assert all(e['source']=='research_replay' for es in initial['events'].values() for e in es)

def test_open_continuity_and_causal_close_stop(initial):
    ob=opening()
    # A gap through the initial stop does not fabricate a market-stop fill:
    # this strategy schedules an exit only after a completed daily close.
    for f in ('o','h','l','c'):ob['bars']['AAPL'][f]=300.
    cal,data,r,phase,_=replay([ob])
    assert phase=='open' and 'AAPL' in r['active']
    assert 'exit_reason' not in r['active']['AAPL']
    ob['phase']='close'
    _,_,r,_,_=replay([ob])
    assert r['active']['AAPL']['exit_reason']=='close_stop'
    p=payload([ob])
    assert {'symbol':'AAPL','side':'SELL','reason':'close_stop','session':'2026-11-03'} in p['plans']
    assert not any(e['date']=='2026-11-02' and e['side']=='SELL' for e in p['events']['AAPL'])

def test_missing_session_and_missing_stock_refused():
    ob=opening();ob['day']='2026-11-03'
    with pytest.raises(ValueError,match='Missing completed'):replay([ob])
    ob=opening();del ob['bars']['MSFT']
    with pytest.raises(ValueError,match='Incomplete cohort'):replay([ob])

def test_split_basis_preserves_economics(initial):
    ob=opening();ob['factors']['AAPL']=2.
    p=payload([ob])
    assert p['held']['AAPL']['units']==pytest.approx(2*initial['held']['AAPL']['units'])
    assert p['held']['AAPL']['stop']==pytest.approx(initial['held']['AAPL']['stop']/2)
    assert p['held']['AAPL']['entry_price']==initial['held']['AAPL']['entry_price']

def test_freshness_and_exchange_calendar(initial):
    now=datetime(2026,10,31,12,tzinfo=ET)
    assert expected(now)==('2026-10-30','close')
    assert status(initial,now,None,now)=='ready'
    assert status(initial,now-timedelta(minutes=8),None,now)=='unavailable'
    assert status(initial,now,'provider failed',now)=='unavailable'
    assert status(initial,now,None,datetime(2026,11,2,9,35,tzinfo=ET))=='unavailable'
    assert session_after('2026-11-25')=='2026-11-27'
    assert session_after('2026-12-24')=='2026-12-28'

def test_no_reconstructed_open_or_unrecorded_close():
    with patch('analysis.momentum_feed.provider.datetime') as dt:
        dt.now.return_value=datetime(2026,11,2,10,tzinfo=ET)
        with pytest.raises(ValueError,match='capture window'):observe('2026-11-02','open')
        dt.now.return_value=datetime(2026,11,2,16,25,tzinfo=ET)
        with pytest.raises(ValueError,match='recorded opening'):observe('2026-11-02','close')

def test_bad_bar_is_not_neutral():
    with pytest.raises(ValueError):validate_bar({'o':10.,'h':9.,'l':8.,'c':9.,'v':100.},'TEST')
