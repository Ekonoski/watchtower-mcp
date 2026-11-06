import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock
import pytest
from analysis.momentum_feed.model import seed, payload, replay, session_after, LIVE
from analysis.momentum_feed.service import status, expected
from analysis.momentum_feed.provider import observe, validate_bar, opening_bar, reconstruct

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


def test_delayed_open_first_completed_bar_with_exact_provenance():
    # Incident 2026-11-03: C had no 09:30 bar. Its first bar was 09:31.
    start = 1793716200000
    raw = {'t':start+60000,'o':122.83,'h':122.83,'l':122.18,'c':122.585,'v':105327.022215}
    assert opening_bar([raw],start,start+120000,'C') == raw
    with pytest.raises(ValueError,match='Missing completed'):
        opening_bar([raw],start,start+60000,'C')
    earlier = {**raw, 't':start}
    assert opening_bar([raw, earlier],start,start+120000,'C') == earlier
    with pytest.raises(ValueError,match='Missing completed'):
        opening_bar([{**raw,'t':start-60000}],start,start+120000,'C')


def test_observe_delayed_open_uses_completed_range_and_preserves_bar():
    from analysis.momentum_feed import provider
    start = 1793716200000
    raw = {'t':start+60000,'o':122.83,'h':122.83,'l':122.18,'c':122.585,'v':105327.022215}
    def rows(path, params):
        if '/aggs/' in path:
            assert path.endswith('/'+str(start)+'/'+str(start+180000-1))
            return [raw]
        return []
    with patch.object(provider,'LIVE',['C']), patch.object(provider,'rows',side_effect=rows), patch.object(provider,'datetime') as dt:
        dt.now.return_value=datetime(2026,11,3,9,33,27,tzinfo=ET)
        dt.fromisoformat.side_effect=datetime.fromisoformat
        ob=observe('2026-11-03','open')
    assert ob['bars']['C']['o']==122.83
    assert ob['opening_bars']['C']==raw
    assert ob['source']=='forward_model'


def test_reconstruction_stays_distinct_from_forward_and_cannot_read_future():
    ob=opening();ob['source']='reconciled_model'
    p=payload([ob])
    assert p['reconciled_sessions']==['2026-11-02']
    assert 'not live observations' in p['note']
    assert all(e['source']=='reconciled_model' for es in p['events'].values() for e in es if e['date']=='2026-11-02')
    with patch('analysis.momentum_feed.provider.datetime') as dt:
        dt.now.return_value=datetime(2026,11,4,13,tzinfo=ET)
        with pytest.raises(ValueError,match='future'):reconstruct('2026-11-05','open')
        with pytest.raises(ValueError,match='settled daily'):reconstruct('2026-11-04','close',ob)


def test_missing_session_stays_actionably_unavailable_without_repeated_incidents():
    from analysis.momentum_feed import service
    current={'asof':'2026-11-02','phase':'close'}
    reason=None
    for hour in (9,13):
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[(True,), (True,{'verified':True},current,reason)]
        cur.fetchall.return_value=[]
        with patch.object(service,'_conn',return_value=conn), patch.object(service,'datetime') as dt, \
             patch('analysis.momentum_feed.provider.observe') as observe_mock, patch.object(service.log,'exception') as logged:
            dt.now.return_value=datetime(2026,11,4,hour,35,tzinfo=ET)
            service.run()
        observe_mock.assert_not_called()
        writes=[c for c in cur.execute.call_args_list if c.args[0].startswith('UPDATE')]
        assert len(writes)==1
        error=writes[0].args[1][0]
        assert '2026-11-03 open' in error and '2026-11-02 close' in error
        assert logged.call_count==(1 if reason is None else 0)
        assert not any(c.args[0].startswith('INSERT') for c in cur.execute.call_args_list)
        reason=error


def test_reconciled_open_close_preserves_source_and_open_price():
    from analysis.momentum_feed import provider
    ob={'day':'2026-11-03','phase':'open','source':'reconciled_model',
        'observed_at':'2026-11-04T13:00:00-05:00','factors':{'C':1.},
        'dividends':[],'bars':{'C':{'o':122.83}},'opening_bars':{'C':{'t':1793716260000}}}
    daily={'C':{'o':122.9,'h':123.,'l':121.,'c':122.,'v':100.}}
    with patch.object(provider,'LIVE',['C']),patch.object(provider,'grouped',return_value=daily),patch.object(provider,'datetime') as dt:
        dt.now.return_value=datetime(2026,11,4,13,tzinfo=ET)
        close=reconstruct('2026-11-03','close',ob)
    assert close['source']=='reconciled_model'
    assert close['bars']['C']['o']==122.83 and close['raw_bars']['C']['o']==122.9
    assert close['opening_bars']==ob['opening_bars']
