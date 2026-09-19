"""Exercise real paper writers, including audit failures after trade writes.

Only clock, tape fetches and database transport are substituted. The real
writer, lifecycle decisions and audit INSERT all run. Closing a psycopg
connection without committing discards its pending transaction.
"""
import datetime as dt
import json
import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import paper_trader as pt, day_bias as db
import test_rsl_v2 as rsl
import test_twotest_book as tt

ET = ZoneInfo('America/New_York')


@contextmanager
def capture(module, fail_event=None):
    instances = []
    init, execute = module._Conn.__init__, module._Cur.execute

    def new(conn, *args):
        init(conn, *args)
        conn.closed = False
        instances.append(conn)

    def run(cur, sql, params=None):
        execute(cur, sql, params)
        if 'INSERT INTO fills_audit' in sql and params[3] == fail_event:
            raise RuntimeError('injected audit failure')

    with patch.object(module._Conn, '__init__', new), \
         patch.object(module._Cur, 'execute', run), \
         patch.object(module._Conn, 'close', lambda c: setattr(c, 'closed', True)):
        yield instances


def audits(conn):
    return [p for s, p in conn.log if s.startswith('INSERT INTO fills_audit')]


def assert_fails(run, module, event):
    with capture(module, event) as instances:
        try:
            run()
        except RuntimeError as e:
            assert str(e) == 'injected audit failure'
        else:
            raise AssertionError('writer swallowed audit failure')
    conn = instances[0]
    assert conn.commits == 0 and conn.closed
    assert any('INSERT INTO paper_trades' in s or 'UPDATE paper_trades' in s
               for s, _ in conn.log), 'failure must occur AFTER a trade mutation'
    assert any(p[3] == event for p in audits(conn))


def rsl_case(kind):
    lv = {'tp1': {'px': 675.5}, 'tp2': None}
    if kind == 'entry':
        return lambda: rsl._run_tick(None, (824, 'META', 'armed', 0, None), None, '09:47', 17)
    trade = (276, dt.datetime(2026, 9, 15, 9, 46, tzinfo=ET), 671.9, None, None)
    return lambda: rsl._run_tick(None, (824, 'META', 'triggered', 669.605, lv), trade,
                                '10:03' if kind == 'partial' else '10:07',
                                34 if kind == 'partial' else 37)


def tt_case(kind):
    if kind == 'entry':
        return lambda: tt._run((900, 'META', 'armed', 0,
                               {'pdh': 100.0, 'pmh': None, 'holes': ['pmh']}),
                              None, None, '09:56', tt._tape())
    lv = {'tp1': {'px': 101.2}, 'tp2': None}
    trade = (777, dt.datetime(2026, 9, 17, 9, 55, tzinfo=ET), 100.6, None, None, None)
    extra = [(100.8, 101.3, 100.7, 101.0)]
    if kind == 'exit':
        extra += [(100.7, 100.8, 100.5, 100.6)]
    if kind == 'disaster':
        extra = [(100.0, 100.1, 99.0, 99.4)]
    bars = tt._tape() + tt._bars(extra, dt.datetime(2026, 9, 17, 9, 56))
    return lambda: tt._run((900, 'META', 'triggered', 100.1499, lv), None,
                           trade, '09:59', bars)


def test_live_v2_entry_partial_and_terminal_failure_close_without_commit():
    for module, case in ((rsl, rsl_case), (tt, tt_case)):
        for kind, event in (('entry', 'entry'), ('partial', 'leg'),
                            ('exit', 'leg'), ('exit', 'exit')):
            assert_fails(case(kind), module, event)


def test_v2_partial_and_missed_tick_terminal_have_individual_legs():
    for case in (rsl_case, tt_case):
        conn, _ = case('partial')()
        rows = audits(conn)
        assert len(rows) == 1 and rows[0][3] == 'leg' and rows[0][12] == 1
        assert json.loads(rows[0][10])['fraction'] == 0.5
        assert json.loads(rows[0][9])['ts']
        conn, _ = case('exit')()  # no prior partial tick: both legs recorded now
        rows = audits(conn)
        assert [(p[3], p[12]) for p in rows] == [('leg', 1), ('leg', 2), ('exit', 0)]
        assert rows[-1][4] == 'weighted_exit_summary'
        evidence = json.loads(rows[-1][10])
        assert evidence['price_basis'] == 'fraction_weighted_legs'
        assert len(evidence['legs']) == 2
        assert conn.commits == 1


def test_disaster_uses_disaster_line_not_structural_stop():
    conn, _ = tt_case('disaster')()
    leg, summary = audits(conn)
    assert leg[5] == leg[6] == summary[5] == summary[6] == round(100.6 * .99, 4)
    assert leg[5] != 100.1499


class Cursor:
    def __init__(self, conn):
        self.conn = conn
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, params=None):
        self.sql = ' '.join(sql.split())
        self.conn.log.append((self.sql, params))
        if 'INSERT INTO fills_audit' in sql and self.conn.fail:
            raise RuntimeError('injected audit failure')
    def fetchall(self):
        if 'GROUP BY s.book' in self.sql:
            return []
        return self.conn.rows
    def fetchone(self):
        if 'INSERT INTO paper_trades' in self.sql or 'RETURNING id' in self.sql:
            return (42,)
        if 'FROM paper_specs' in self.sql:
            return self.conn.spec
        if 'FROM daily_prices' in self.sql:
            return (101.0,)
        return self.conn.bar


class Connection:
    def __init__(self, fail):
        self.fail, self.log, self.commits, self.closed = fail, [], 0, False
        self.rows, self.spec, self.bar = [], None, None
    def cursor(self):
        return Cursor(self)
    def commit(self):
        self.commits += 1
    def close(self):
        self.closed = True


def run_paper(kind, fail=False, direction='long'):
    conn = Connection(fail)
    now = dt.datetime(2026, 9, 21, 16 if kind == 'settle' else 11, 30, tzinfo=ET)
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    clock = SimpleNamespace(datetime=Clock, time=dt.time, timedelta=dt.timedelta)
    ts = now.replace(hour=15, minute=45) if kind == 'settle' else now.replace(hour=10, minute=45)
    target, stop, entry = (110., 90., 100.) if direction == 'long' else (90., 110., 100.)
    op = 112. if direction == 'long' else 88.
    conn.bar = (ts, op, op, op + 1, op - 1)
    if kind == 'settle':
        conn.rows = [('TEST', direction, stop, target, 42, entry,
                      ts - dt.timedelta(days=1), 'swing')]
        fn = pt.run_swing_close_settle
    else:
        entering = kind == 'entry'
        conn.rows = [(1, 'gamma', 'TEST', direction, 'test', entry, stop, target,
                      'armed' if entering else 'triggered', ts - dt.timedelta(hours=1),
                      None if entering else 42, None if entering else entry,
                      None if entering else ts - dt.timedelta(days=1), None, 'n/a')]
        if not entering:
            row = list(conn.rows[0]); row[1] = 'swing'; conn.rows = [tuple(row)]
        else:
            conn.bar = (ts, 100., 100.1, 101., 99.)
            row = list(conn.rows[0]); row[7] = 120.; conn.rows = [tuple(row)]
        fn = pt.run_trigger_loop
    with patch.object(pt, 'dt', clock), patch.object(pt, 'get_db_connection', lambda: conn), \
         patch.object(pt, 'write_intraday_specs', lambda c: None), \
         patch.object(pt, 'run_binary_shadow', lambda *a: None), \
         patch.object(pt, '_persist_spec_bars', lambda *a: None), \
         patch.object(pt, '_last_closed_15m', lambda *a: [conn.bar]):
        try:
            fn()
        except RuntimeError:
            if not fail:
                raise
    return conn


def run_daybias(kind, fail=False):
    conn = Connection(fail)
    now = dt.datetime(2026, 9, 21, 11, 30, tzinfo=ET)
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    clock = SimpleNamespace(datetime=Clock, time=dt.time, timedelta=dt.timedelta)
    conn.spec = (1, 'armed' if kind == 'entry' else 'triggered', 100., 99.25)
    conn.rows = [(42, 100., 99.25, now.date())]
    result = {'state': 'filled' if kind == 'entry' else 'stopped', 'at': now,
              'entry': 100., 'stop': 99.25, 'stop_at': now, 'stop_px': 99.}
    with patch.object(db, 'dt', clock), patch.object(db, 'decide', lambda *a: result), \
         patch.object(pt, 'get_db_connection', lambda: conn), \
         patch.object(pt, '_last_closed_15m', lambda *a: [(now, 101., 100., 102., 99.)]), \
         patch.object(pt, '_persist_spec_bars', lambda *a: None):
        try:
            (db.run_daybias_settle if kind == 'settle' else db.run_daybias_loop)()
        except RuntimeError:
            if not fail:
                raise
    return conn


def test_paper_and_daybias_actual_writers_refuse_commit_on_audit_failure():
    for run in (run_paper, run_daybias):
        for kind in ('entry', 'exit', 'settle'):
            good = run(kind)
            assert audits(good) and good.commits > 0
            bad = run(kind, fail=True)
            assert bad.commits == 0 and bad.closed
            assert audits(bad)
            assert any('INSERT INTO paper_trades' in s or 'UPDATE paper_trades' in s
                       for s, _ in bad.log)


def test_swing_loop_and_settle_record_target_gap_bar_both_directions():
    for kind in ('exit', 'settle'):
        for direction, expected, got in (('long', 110., 112.), ('short', 90., 88.)):
            conn = run_paper(kind, direction=direction)
            row, = audits(conn)
            assert row[5:8] == (expected, got, True)
            assert json.loads(row[9])['open'] == got


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for test in tests:
        test()
        print('ok', test.__name__)
    print(f'{len(tests)} tests passed')
