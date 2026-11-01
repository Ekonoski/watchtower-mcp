"""Regression checks for the real-time entitlement / scheduled scan boundary."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from analysis import momentum_scan as scan

ET = ZoneInfo('America/New_York')


class SessionTiming(unittest.TestCase):
    def at(self, hour, minute=0, second=0):
        return scan._session_now(datetime(2026, 11, 2, hour, minute, second, tzinfo=ET))

    def test_premarket_starts_at_four(self):
        self.assertEqual(self.at(3, 59, 59), ('closed', 0))
        self.assertEqual(self.at(4), ('premarket', .05))

    def test_regular_session_starts_at_930_not_945(self):
        self.assertEqual(self.at(9, 29, 59), ('premarket', .05))
        self.assertEqual(self.at(9, 30), ('regular', .05))
        self.assertEqual(self.at(9, 31), ('regular', .05))

    def test_volume_denominator_has_no_fifteen_minute_offset(self):
        session, fraction = self.at(10, 30)
        self.assertEqual(session, 'regular')
        self.assertAlmostEqual(fraction, 60 / 390)

    def test_close_is_at_sixteen_not_sixteen_fifteen(self):
        self.assertEqual(self.at(15, 59, 59)[0], 'regular')
        self.assertEqual(self.at(16), ('closed', 1))

    def test_utc_conversion_tracks_dst(self):
        for stamp in ('2026-10-30T13:30:00+00:00', '2026-11-02T14:30:00+00:00'):
            self.assertEqual(scan._session_now(datetime.fromisoformat(stamp)), ('regular', .05))

    def test_weekend_closed(self):
        self.assertEqual(scan._session_now(datetime(2026, 11, 1, 15, tzinfo=timezone.utc)), ('closed', 0))

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            scan._session_now(datetime(2026, 11, 2, 10, 30))

    def test_writer_uses_corrected_denominator(self):
        # A fixed 1M current volume / 1M daily average at 10:30 ET yields
        # 6.5x pace, not the old 8.67x created by subtracting 15 minutes.
        fixed = datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
        snapshot = SimpleNamespace(ticker='TEST', day=SimpleNamespace(
            close=5.5, open=5.1, volume=1_000_000),
            prev_day=SimpleNamespace(close=5), min=None)
        client = MagicMock()
        client.get_snapshot_all.return_value = [snapshot]
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [
            [('TEST', 1_000_000, 4, 35)],
            [('TEST', 10_000_000, None, 1_000_000, 4, None)], []]
        with patch.object(scan, 'datetime', Clock), \
             patch('analysis.polygon_data.get_client', return_value=client), \
             patch('screen.reversal_screen._conn', return_value=conn), \
             patch.object(scan, '_news_map', return_value={}):
            result = scan.run_momentum_scan()
        self.assertEqual(result['session'], 'regular')
        saved = cur.executemany.call_args.args[1][0]
        self.assertEqual(saved[5], 6.5)
        self.assertEqual(saved[15], 'regular')
        conn.commit.assert_called_once()

    def test_api_does_not_present_save_time_as_event_time(self):
        from dashboard.api import _momentum_rows
        saved_at = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)
        row = ('TEST', 5.5, 10, 4, 1_000_000, 6.5, 10_000_000,
               None, False, '', {}, 3, 20, False, False, 'regular', saved_at,
               None, None, None, None, None, None)
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [row]
        cur.fetchone.return_value = (saved_at, 'regular')
        with patch('screen.reversal_screen._conn', return_value=conn):
            result = _momentum_rows()['rows'][0]
        self.assertEqual(result['scanned_at'], saved_at.isoformat())
        self.assertIsNone(result['market_data_at'])
        self.assertEqual(result['freshness_status'], 'unavailable')
        self.assertEqual(result['relvol_method'], 'approximate_daily_volume_pace')


if __name__ == '__main__':
    unittest.main()
