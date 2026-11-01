import unittest
from analysis.scanner_continuation import Continuations,provider_runners
from analysis.live_tape import TapeBook
from datetime import datetime
from zoneinfo import ZoneInfo

class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,2,7,tzinfo=ZoneInfo('America/New_York')).timestamp()
        self.events=[];self.c=Continuations(lambda k,p,t=None:self.events.append((k,p,t)))
        self.book=TapeBook();self.book.continuations=self.c
        self.result={'target_session':'2026-11-02','sessions':['2026-10-30'],
            'coverage':[{'session':'2026-10-30','daily_symbols':8108}],'source_note':'fixture',
            'runners':[{'ticker':'OLD','runner_session':'2026-10-30','last_close':5,'peak_gain_pct':25}]}
        self.c.apply(self.result,self.now)
    def live(self,move=4):
        self.book.rows['OLD']={'ticker':'OLD','price':5.2,'market_at':self.now,'received_at':self.now,
            'move_1m':move,'move_5m':None,'move_10m':None,'chg_pct':4,'volume_second':100}
    def volume(self,one=20000,five=40000):
        self.book.aggregate_windows.reading=lambda ticker,now,seconds=300:{'volume':one if seconds==60 else five,
            'as_of':self.now,'complete':True,'source':'fixture'}
    def test_quiet_carried_name_is_not_current_signal(self):
        self.c.sample(self.book,self.now)
        row=self.c.views['preferred']['rows'][0]
        self.assertEqual(row['continuation']['state'],'waiting for data')
        self.assertIsNone(row['market_at'])
        self.assertFalse(row['continuation']['price_is_live'])
    def test_volume_and_momentum_return_then_failure_kept(self):
        self.live(move=0);self.volume(one=1000);self.c.sample(self.book,self.now)
        self.live();self.volume();self.c.sample(self.book,self.now+1)
        row=self.c.views['preferred']['rows'][0]
        self.assertEqual(row['continuation']['state'],'volume + momentum')
        events=[p for k,p,t in self.events if k=='continuation_state']
        self.assertFalse(events[-1]['baseline'])
        self.live(move=-4);self.volume(one=1000);self.c.sample(self.book,self.now+2)
        self.assertEqual(self.c.views['preferred']['total'],1)
        self.assertEqual(self.c.views['preferred']['rows'][0]['continuation']['state'],'quiet / cooling')
    def test_missing_volume_is_not_negative_evidence(self):
        self.live();self.c.sample(self.book,self.now)
        c=self.c.views['preferred']['rows'][0]['continuation']
        self.assertIsNone(c['volume_returning'])
        self.assertTrue(c['momentum_returning'])
    def test_today_and_expired_runs_are_excluded(self):
        self.result['runners']+=[{'ticker':'TODAY','runner_session':'2026-11-02'},
                                 {'ticker':'EXPIRED','runner_session':'2026-10-23'}]
        self.c.apply(self.result,self.now)
        self.assertEqual(set(self.c.seeds),{'OLD'})
    def test_reconnect_and_sampling_gaps_are_disclosed(self):
        self.live();self.volume();self.c.sample(self.book,self.now)
        self.c.sample(self.book,self.now+4)
        self.assertTrue(self.c.views['preferred']['rows'][0]['continuation']['coverage_gap'])
        self.book.reset_windows();self.c.sample(self.book,self.now+5)
        self.assertTrue([p for k,p,t in self.events if k=='continuation_state'][-1]['baseline'])
    def test_stale_universe_does_not_claim_return(self):
        self.live();self.volume();self.c.status={'state':'unavailable'}
        self.c.sample(self.book,self.now)
        row=self.c.views['preferred']['rows'][0]
        self.assertIsNone(row['continuation']['momentum_returning'])
    def test_observed_runner_persisted_for_next_day_not_today(self):
        self.live();self.book.rows['OLD']['chg_pct']=25
        self.book.volume_state['OLD']={'value':300000,'market_at':self.now}
        self.c.sample(self.book,self.now)
        candidates=[p for k,p,t in self.events if k=='continuation_candidate']
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['runner_session'],'2026-11-02')
        self.c.sample(self.book,self.now+1)
        self.assertEqual(len([p for k,p,t in self.events if k=='continuation_candidate']),1)
    def test_lists_include_continuations_without_changing_other_families(self):
        self.live();self.c.sample(self.book,self.now)
        scanner=self.book.scanner_snapshot(self.now)['scanner']
        self.assertEqual(scanner['preferred']['continuation']['rows'][0]['ticker'],'OLD')
        self.assertEqual(scanner['preferred']['leaders']['rows'][0]['ticker'],'OLD')

    def test_market_wide_seed_retains_failed_close_and_uses_prior_session(self):
        class Provider:
            def get(self,url):
                if '2026-10-29' in url:return {'results':[{'T':'TNMG','c':4}]}
                return {'results':[{'T':'TNMG','h':5,'c':3,'v':1000000},
                                   {'T':'IPO','h':10,'c':8,'v':1000000}]}
        rows,coverage=provider_runners(['2026-10-30','2026-10-29'],Provider())
        self.assertEqual([r['ticker'] for r in rows],['TNMG'])
        self.assertEqual(rows[0]['peak_gain_pct'],25)
        self.assertEqual(rows[0]['last_close'],3)
        self.assertEqual(coverage[0]['provider_symbols'],2)

if __name__=='__main__':unittest.main()
