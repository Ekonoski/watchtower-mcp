import unittest
from analysis.scanner_focus import ScannerFocus
from analysis.live_tape import TapeBook


def row(ticker='OLD', stamp=1000, price=5, change=15, move=4):
    return dict(ticker=ticker, market_at=stamp, price=price, chg_pct=change, move_1m=move)


class FocusTests(unittest.TestCase):
    def test_price_filter_precedes_cap(self):
        rows=[row(str(i), price=30, change=100+i) for i in range(150)]+[row('TARGET')]
        result=ScannerFocus().views(rows, 1000, limit=100)
        self.assertEqual(result['preferred']['leaders']['rows'][0]['ticker'], 'TARGET')
        self.assertEqual(result['all']['leaders']['omitted'], 51)

    def test_pullback_survives_next_to_new_arrival(self):
        focus=ScannerFocus();focus.observe(row())
        pullback=row(stamp=1020, change=8, move=-2);focus.observe(pullback)
        newcomer=row('NEW', stamp=1020);focus.observe(newcomer)
        result=focus.views([pullback,newcomer], 1020)['preferred']['focus']['rows']
        self.assertEqual([r['ticker'] for r in result], ['NEW','OLD'])
        self.assertEqual(result[1]['attention_state'],'holding')
        self.assertEqual(result[1]['hold_remaining_seconds'],280)
        self.assertEqual(result[1]['move_1m'],-2)

    def test_failed_mover_expires_without_refresh_on_decline(self):
        focus=ScannerFocus();focus.observe(row())
        failure=row(stamp=1299, change=-10, move=-5);focus.observe(failure)
        self.assertEqual(focus.views([failure],1299)['preferred']['focus']['total'],1)
        self.assertEqual(focus.views([failure],1301)['preferred']['focus']['total'],0)

    def test_stale_price_is_not_live_momentum(self):
        focus=ScannerFocus();focus.observe(row())
        views=focus.views([row()],1020)['preferred']
        self.assertEqual(views['fresh']['total'],0)
        self.assertEqual(views['leaders']['total'],0)
        self.assertTrue(views['focus']['rows'][0]['stale'])

    def test_unknown_reference_not_assumed_zero(self):
        focus=ScannerFocus();unknown=row(change=None,move=None);focus.observe(unknown)
        views=focus.views([unknown],1000)['preferred']
        for key in ('fresh','leaders','focus'):self.assertEqual(views[key]['total'],0)
        self.assertEqual(views['warming'],1)
        self.assertEqual(views['missing_day_reference'],1)

    def test_return_after_expiry_is_new_arrival(self):
        focus=ScannerFocus();focus.observe(row());focus.observe(row(stamp=1400))
        self.assertEqual(focus.views([row(stamp=1400)],1400)['preferred']['focus']['rows'][0]['first_seen'],1400)

    def test_reconnect_clears_attention(self):
        book=TapeBook();book.focus.observe(row());book.reset_windows()
        self.assertEqual(book.focus.views([row()],1000)['preferred']['focus']['total'],0)

    def test_real_ingest_populates_views(self):
        book=TapeBook();book.previous_close['TEST']=4
        base=1793104800
        for i in range(61):
            book.ingest(dict(ev='T',sym='TEST',p=5 if i<60 else 5.2,t=(base+i)*1000,
                             s=100,q=i+1,c=[12]),base+i+.1)
        result=book.snapshot(base+60.1)['scanner']['preferred']
        self.assertEqual(result['leaders']['rows'][0]['ticker'],'TEST')
        self.assertEqual(result['fresh']['rows'][0]['ticker'],'TEST')
        self.assertEqual(result['focus']['rows'][0]['first_seen'],base)


if __name__=='__main__':unittest.main()
