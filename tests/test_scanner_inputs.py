import unittest
from analysis.scanner_inputs import AggregateWindows,float_reading,rvol_reading
from analysis.live_tape import TapeBook


class InputTests(unittest.TestCase):
    def test_float_requires_real_float_and_point_in_time_provenance(self):
        row={'value':1_000_000,'source':'fixture-provider','effective_at':1000,'received_at':1001,'metric':'shares_outstanding'}
        self.assertIsNone(float_reading(row,1002)['qualifies_under_20m'])
        row['metric']='free_float'
        self.assertTrue(float_reading(row,1002)['qualifies_under_20m'])
        self.assertIsNone(float_reading(row,1000)['qualifies_under_20m'])
        self.assertIsNone(float_reading(row,1002+15*86400)['qualifies_under_20m'])
        row['value']=25_000_000
        self.assertFalse(float_reading(row,1002)['qualifies_under_20m'])

    def test_rvol_definitions_and_percent_units(self):
        common=dict(source='fixture',complete=True,as_of=1000,received_at=1001,
                    method='trailing_aggregate_window',trade_scope='provider_eligible_aggregate_trades',
                    session_scope='extended',window_seconds=300,adjustment_basis='unadjusted')
        current={**common,'volume':600,'session_date':'2026-11-02'}
        baseline={**common,'mean_volume':200,'sample_sessions':14,'history_end_session':'2026-10-30'}
        result=rvol_reading(current,baseline,1002)
        self.assertEqual(result['ratio'],3)
        self.assertEqual(result['percent'],300)
        self.assertIsNone(rvol_reading(current,{**baseline,'trade_scope':'all_trades'},1002)['ratio'])
        self.assertIsNone(rvol_reading(current,{**baseline,'history_end_session':'2026-11-02'},1002)['ratio'])
        self.assertIsNone(rvol_reading(current,{**baseline,'received_at':1003},1002)['ratio'])
        self.assertIsNone(rvol_reading(current,None,1002)['ratio'])
        self.assertEqual(rvol_reading({**current,'volume':0},baseline,1002)['ratio'],0)

    def event(self,sec,volume=100):
        return dict(ev='A',sym='TEST',s=sec*1000,e=sec*1000+999,v=volume)

    def test_aggregate_window_warmup_roll_and_revision(self):
        windows=AggregateWindows()
        for i in range(299):windows.ingest(self.event(i),i+1)
        self.assertIsNone(windows.reading('TEST',299)['volume'])
        windows.ingest(self.event(299),300)
        self.assertEqual(windows.reading('TEST',300)['volume'],30000)
        windows.ingest(self.event(299,200),300)
        self.assertEqual(windows.reading('TEST',300)['volume'],30100)
        windows.ingest(self.event(300),301)
        self.assertEqual(windows.reading('TEST',301)['volume'],30100)
        self.assertEqual(windows.reading('TEST',320)['age_seconds'],19)
        windows.reset();self.assertIsNone(windows.reading('TEST',321)['volume'])

    def test_bad_aggregate_is_not_accepted(self):
        windows=AggregateWindows()
        for event in (self.event(100),self.event(0,-1),{**self.event(0),'s':.5}):
            windows.ingest(event,1)
        self.assertIsNone(windows.reading('TEST',1)['volume'])

    def test_known_high_does_not_use_future_snapshot(self):
        book=TapeBook();base=1793104800
        book.high_references['TEST']={'high':9,'as_of':base+5,'session':'2026-10-27'}
        def put(second,price):
            book.ingest(dict(ev='T',sym='TEST',p=price,t=(base+second)*1000,s=100,q=second+1,c=[12]),base+second+.1)
        put(0,5)
        self.assertIsNone(book.rows['TEST']['new_known_high'])
        self.assertEqual(book.rows['TEST']['known_high'],5)
        put(1,6);self.assertTrue(book.rows['TEST']['new_known_high'])
        put(5,7)
        self.assertEqual(book.rows['TEST']['known_high'],9)
        self.assertFalse(book.rows['TEST']['new_known_high'])
        self.assertIn('unverified',book.rows['TEST']['hod_coverage'])

    def test_stale_volume_does_not_top_live_volume_list(self):
        book=TapeBook();base=1793104800
        for i in range(320):
            book.ingest(dict(ev='T',sym='TEST',p=5,t=(base+i)*1000,s=100,q=i+1,c=[12]),base+i+.1)
            if i<300:
                e=self.event(base+i);book.ingest(e,base+i+1)
        fresh=book.snapshot(base+300)['scanner']['preferred']['volume']
        # Current row comes from t+319, so it cannot qualify in an earlier snapshot.
        self.assertEqual(fresh['total'],0)
        self.assertEqual(book.snapshot(base+319.1)['scanner']['preferred']['volume']['total'],0)


if __name__=='__main__':unittest.main()
