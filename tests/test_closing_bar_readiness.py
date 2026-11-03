"""Closing capture reports recorded final bars, not merely nonempty fetches."""
import datetime as dt
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import paper_trader as pt


class Conn:
    def __init__(self):
        self.ticker = None
        self.closed = False
        self.rollbacks = 0
        self.bars = {}
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, params=None):
        if 'max(ts)' in sql:
            self.ticker = params[0]
    def fetchall(self):
        return [(t,) for t in ['MORNING', 'EMPTY', 'ERROR', 'CLOSE', 'STORED']]
    def fetchone(self):
        return (self.bars.get(self.ticker),)
    def rollback(self):
        self.rollbacks += 1
    def close(self):
        self.closed = True


def test_missing_and_fetch_failure_do_not_mask_other_closing_bars():
    conn = Conn()
    now = dt.datetime(2026, 9, 21, 16, 7, tzinfo=pt.ET)
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    morning = now.replace(hour=11, minute=30)
    final = now.replace(hour=15, minute=45)
    conn.bars['STORED'] = final
    persisted = []
    def fetch(tk):
        if tk == 'ERROR':
            raise RuntimeError('provider unavailable')
        if tk in ('EMPTY', 'STORED'):
            return []
        return [(morning if tk == 'MORNING' else final, 10, 10, 11, 9)]
    def persist(c, tk, day, bars):
        persisted.append(tk)
        c.bars[tk] = bars[-1][0]
    with patch.object(pt, 'dt', SimpleNamespace(datetime=Clock)), \
         patch.object(pt, 'get_db_connection', lambda: conn), \
         patch.object(pt, '_last_closed_15m', fetch), \
         patch.object(pt, '_persist_spec_bars', persist):
        result = pt.persist_closing_bars()
    assert result['ready'] == 2 and result['total'] == 5
    assert result['failed'] == ['ERROR']
    assert len(result['missing']) == 2
    assert result['missing'][0].startswith('MORNING (last RTH bar 2026-09-21T11:30:00')
    assert result['missing'][1] == 'EMPTY (last RTH bar none)'
    assert persisted == ['MORNING', 'CLOSE']  # never fabricates a missing bar
    assert conn.rollbacks == 1 and conn.closed


if __name__ == '__main__':
    test_missing_and_fetch_failure_do_not_mask_other_closing_bars()
    print('ok: closing-bar readiness and per-ticker failure isolation')
