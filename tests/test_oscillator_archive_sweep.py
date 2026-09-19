"""Archived rows stay historical; active and exposed symbols still get fetched."""
import os
import sys
from unittest.mock import patch
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import oscillator as osc
from screen import reversal_screen


class Conn:
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params):
        self.sql, self.params = sql, params
    def fetchall(self): return [('ARCHIVED',)]
    def commit(self): self.committed = True
    def close(self): self.closed = True


def test_sweep_preserves_archived_rows_and_surfaces_active_holes():
    conn = Conn()
    stale = [(tk, tf, '2026-08-01') for tk in ('ARCHIVED', 'ACTIVE', 'EXPOSED')
             for tf in ('1h', '4h')]
    calls = []
    def fetch(tk, tf):
        calls.append((tk, tf))
        return pd.DataFrame()
    with patch.object(reversal_screen, '_conn', lambda: conn), \
         patch.object(osc, '_pattern_context', lambda c: {}), \
         patch.object(osc, 'stale_intraday_rows', lambda c: stale), \
         patch.object(osc, 'fetch_intraday_fresh', fetch), \
         patch.object(osc, '_store') as write:
        result = osc.refresh_stale_intraday()
    assert result == {'refreshed': 0, 'unresolved': 4, 'archived': 2}
    assert calls == [(tk, tf) for tk in ('ACTIVE', 'EXPOSED') for tf in ('1h', '4h')]
    write.assert_not_called()
    assert conn.committed and conn.closed


if __name__ == '__main__':
    test_sweep_preserves_archived_rows_and_surfaces_active_holes()
    print('ok: archived rows retained, active/exposed holes remain unresolved')
