import unittest
from analysis.live_tape import TapeBook
from analysis.scanner_list_history import ListHistory


class ListHistoryTests(unittest.TestCase):
    def setUp(self):
        self.book=TapeBook();self.events=[];self.now=1793104800
        self.history=ListHistory(lambda kind,payload,at:self.events.append((kind,payload,at)))
    def trade(self,ticker,price=5,previous=4,at=None):
        at=self.now if at is None else at
        self.book.previous_close[ticker]=previous
        self.book.ingest(dict(ev='T',sym=ticker,p=price,s=100,t=at*1000,q=int(at*1000),c=[12]),at+.01)
    def capture(self,at=None):
        at=self.now+.1 if at is None else at
        snap=self.book.snapshot(at)
        self.history.capture(snap,at,self.book.window_generation,'receiving')
        return snap
    def memberships(self,band='preferred',family='leaders'):
        return [e[1] for e in self.events if e[0]=='list_membership' and e[1]['band']==band and e[1]['family']==family]
    def test_new_leader_is_recorded_without_move_alert(self):
        self.trade('OLD');self.capture()
        self.trade('NEW',at=self.now+1);self.capture(self.now+1.1)
        row=self.memberships()[0]['entered'][0]
        self.assertEqual(row['ticker'],'NEW')
        self.assertEqual(row['arrival'],'new_in_observed_scope')
        self.assertEqual(row['row']['chg_pct'],25)
        self.assertEqual(row['row']['float_reference']['status'],'unavailable')
        self.assertEqual(len(self.book.alerts),0)
    def test_ranks_caps_and_all_price_bands_match_ui(self):
        for i in range(110):self.trade(f'S{i:03}',price=30+i,previous=10)
        self.trade('CHEAP',price=5)
        snap=self.capture()
        saved=self.events[-1][1]
        for band,group in snap['scanner'].items():
            for family in ('leaders','fresh','focus','volume'):
                self.assertEqual(saved['views'][band][family]['tickers'],[r['ticker'] for r in group[family]['rows']])
                self.assertEqual(saved['views'][band][family]['omitted'],group[family]['omitted'])
        self.assertEqual(saved['views']['all']['leaders']['omitted'],11)
        self.assertEqual(saved['views']['preferred']['leaders']['tickers'],['CHEAP'])
    def test_departure_return_and_failure_are_not_erased(self):
        self.trade('OLD');self.capture()
        for sec in range(1,18):self.capture(self.now+sec+.1)
        self.assertEqual(self.memberships()[-1]['left'][0]['ticker'],'OLD')
        self.trade('OLD',at=self.now+18);self.capture(self.now+18.1)
        self.assertEqual(self.memberships()[-1]['entered'][0]['arrival'],'returning')
        self.trade('OLD',price=3,at=self.now+19);self.capture(self.now+19.1)
        self.assertEqual(self.memberships()[-1]['left'][0]['ticker'],'OLD')
        self.capture(self.now+31)
        saved=self.events[-1][1]
        self.assertEqual(saved['rows_by_ticker']['OLD']['chg_pct'],-25)
        self.assertEqual(saved['rows_by_ticker']['OLD']['attention_state'],'holding')
    def test_reconnect_is_baseline_not_new_arrival(self):
        self.trade('OLD');self.capture()
        self.book.reset_windows();self.trade('NEW',at=self.now+1)
        self.capture(self.now+1.1)
        self.assertEqual(self.memberships(),[])
        self.assertTrue(self.events[-1][1]['baseline'])
    def test_gap_does_not_claim_precise_arrival(self):
        self.trade('OLD');self.capture()
        self.trade('NEW',at=self.now+10);self.capture(self.now+10.1)
        event=self.memberships()[0]
        self.assertTrue(event['coverage_gap'])
        self.assertEqual(event['entered'][0]['arrival'],'after_gap')
        self.assertAlmostEqual(event['sample_gap_seconds'],10)
    def test_rank_only_changes_checkpointed_without_inventing_entries(self):
        self.trade('ONE',price=5);self.trade('TWO',price=4.5);self.capture()
        self.trade('TWO',price=6,at=self.now+1);self.capture(self.now+1.1)
        self.assertEqual(self.memberships(),[])
        self.trade('ONE',at=self.now+30);self.trade('TWO',price=6,at=self.now+30)
        self.capture(self.now+30.2)
        self.assertEqual(self.events[-1][1]['views']['preferred']['leaders']['tickers'],['TWO','ONE'])

    def test_lazy_reference_enrichment_preserves_existing_ui_rules(self):
        import copy
        for i in range(120):self.trade(f'S{i}',price=3+i*.2)
        now=self.now+.1
        original=copy.deepcopy(self.book.focus).views([self.book.with_volume(r,now) for r in self.book.rows.values()],now)
        self.assertEqual(self.book.scanner_snapshot(now)['scanner'],original)


if __name__=='__main__':unittest.main()
