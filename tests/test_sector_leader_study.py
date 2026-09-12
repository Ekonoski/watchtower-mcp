"""The sector-leader study (2026-09-12), pinned:
  1. Funds are holes, not Financial Services: an Asset Management row
     maps to no ETF; a Technology stock maps to XLK.
  2. prior_index reads the last bar STRICTLY before the breakout date —
     no lookahead, by construction.
  3. The legs: near_high tolerance and min-bars hole, vol_expansion
     warmup/zero holes, green_red_days skips dates SPY lacks,
     rank_among leaves None out of the count, has_defect window.
  4. etf_legs ranks only ETFs with data that day (n_etfs beside it).
  5. One definition (ema_series / series_defects imported, never
     redefined) and writes only its own tables — a fake-connection
     smoke run drives stage 1 end to end so a bad SQL string cannot
     ship silently (the twotest ORB-clock lesson).
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import sector_leader_study as sl  # noqa: E402

D = dt.date


def test_fund_and_sector_mapping():
    assert sl.sector_etf("Financial Services", "Asset Management - Bonds") is None
    assert sl.sector_etf("Financial Services", "Shell Companies") is None
    assert sl.sector_etf("Financial Services", "Banks - Regional") == "XLF"
    assert sl.sector_etf("Technology", "Semiconductors") == "XLK"
    assert sl.sector_etf(None, None) is None
    assert sl.is_fund("Asset Management") and not sl.is_fund("Banks") and not sl.is_fund(None)


def test_prior_index_is_strictly_before():
    dates = [D(2026, 9, 8), D(2026, 9, 9), D(2026, 9, 10), D(2026, 9, 11)]
    assert sl.prior_index(dates, D(2026, 9, 10)) == 1          # 9/9, not the breakout bar itself
    assert sl.prior_index(dates, D(2026, 9, 12)) == 3          # a weekend breakout reads Friday
    assert sl.prior_index(dates, D(2026, 9, 8)) is None        # nothing precedes the first bar


def test_legs():
    closes = [100 + i for i in range(300)]
    assert abs(sl.ret(closes, 299, 21) - (399 / 378 - 1)) < 1e-12
    assert sl.ret(closes, 10, 21) is None
    assert sl.near_high(closes, 299) is True                   # rising tape is at its high
    assert sl.near_high(closes, 100) is None                   # under 126 bars: hole
    flat = [100.0] * 300
    flat[299] = 96.0
    assert sl.near_high(flat, 299) is False                    # 4% off the high, tol 3%
    vols = [100.0] * 300
    vols[280:300] = [200.0] * 20
    assert abs(sl.vol_expansion(vols, 299) - (200 / ((40 * 100 + 20 * 200) / 60))) < 1e-9
    assert sl.vol_expansion(vols, 30) is None
    assert sl.vol_expansion([0.0] * 300, 299) is None
    dates = [D(2026, 1, 1) + dt.timedelta(days=i) for i in range(20)]
    stk = [100 + i for i in range(20)]                         # up every day
    spy = {d: 100 - i for i, d in enumerate(dates)}            # down every day
    assert sl.green_red_days(dates, stk, spy, 19) == 10
    del spy[dates[15]]                                         # a date SPY lacks cannot count either way
    assert sl.green_red_days(dates, stk, spy, 19) == 8
    assert sl.green_red_days(dates, stk, spy, 5) is None
    assert sl.rank_among({"XLK": 0.05, "XLF": None, "XLE": 0.10}) == {"XLE": 1, "XLK": 2}
    dd = [D(2026, 3, 1)]
    ds = [D(2026, 1, 1) + dt.timedelta(days=i) for i in range(120)]
    assert sl.has_defect(dd, ds, 70) is True                   # 3/1 is day 59, inside (day 7, day 70]
    assert sl.has_defect(dd, ds, 50) is False
    assert sl.era(D(2015, 12, 31)) == "pre2016" and sl.era(D(2016, 1, 1)) == "post2016"


def test_etf_legs_rank_only_etfs_with_data():
    def series(start, n, slope):
        rows = [(start + dt.timedelta(days=i), 100 + slope * i) for i in range(n)]
        return sl._Series(rows)
    d0 = D(2024, 1, 1)
    bench = {"SPY": series(d0, 120, 0.1), "QQQ": series(d0, 120, 0.2)}
    for k, etf in enumerate(sl.ETFS):
        bench[etf] = series(d0, 120, 0.05 * (k + 1))
    bench["XLC"] = series(d0 + dt.timedelta(days=200), 30, 1.0)  # starts after the date: no data
    out = sl.etf_legs(bench, d0 + dt.timedelta(days=119), {})
    assert out["n_etfs"] == len(sl.ETFS) - 1 and "XLC" not in out["ranks"]
    assert out["legs"]["XLC"]["rs21"] is None
    best = max(sl.ETFS, key=lambda e: sl.ETFS.index(e) if e != "XLC" else -1)
    assert out["ranks"][best] == 1


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        s = " ".join(sql.split())
        if "FROM scheduler_job_claims" in s:
            self._rows = []
        elif "SELECT trade_date, close FROM daily_prices" in s:
            tk = params[0]
            self._rows = self.conn.series.get(tk, [])
        elif "SELECT DISTINCT pb.ticker, t.sector, t.industry" in s:
            self._rows = [] if self.conn.done else [("ACME", "Technology", "Semiconductors")]
        elif "FROM pattern_backtest WHERE ticker=" in s:
            self._rows = [(1, D(2024, 6, 3), "higher_low", 2, True, 1.4),
                          (2, D(2024, 1, 5), "cup_handle", None, False, -1.0)]   # inside warmup: legs None
        elif "SELECT trade_date, close, COALESCE(volume, 0) FROM daily_prices" in s:
            self._rows = [(d, c, 1000.0) for d, c in self.conn.series["ACME"]]
        elif "INSERT INTO sector_leader_progress" in s:
            self.conn.done = True
        else:
            self._rows = []

    def executemany(self, sql, rows):
        self.conn.sql.append(sql)
        self.conn.inserted.extend(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self):
        d0 = D(2024, 1, 1)
        base = [(d0 + dt.timedelta(days=i), 100.0 + 0.1 * i) for i in range(200)]
        self.series = {"SPY": base, "QQQ": base}
        for etf in sl.ETFS:
            self.series[etf] = base
        self.series["ACME"] = [(d, c * 1.5) for d, c in base]
        self.sql, self.inserted, self.done = [], [], False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        raise AssertionError("stage 1 raised inside a ticker")


def test_stage1_smoke_and_write_scope():
    src = inspect.getsource(sl)
    assert "from analysis.tapeentry_study import ema_series" in src and "def ema_series(" not in src
    assert "from analysis.beat_spy import series_defects" in src and "def series_defects(" not in src
    for forbidden in ("INSERT INTO pattern_backtest", "INSERT INTO paper_", "DELETE FROM",
                      "UPDATE pattern_backtest", "UPDATE paper_", "INSERT INTO sector_study"):
        assert forbidden not in src, forbidden
    assert "UPDATE sector_leader_events" in src and "INSERT INTO sector_leader_events" in src
    conn = _FakeConn()
    assert sl._stage1(conn, __import__("time").time()) is True
    rows = {r[0]: r for r in conn.inserted}
    assert set(rows) == {1, 2}
    r1 = rows[1]
    assert r1[3] == "XLK" and r1[4] is False and r1[6] == D(2024, 6, 2) and r1[7] == "post2016"
    assert r1[14] is not None and r1[15] == len(sl.ETFS)            # ranked among all SPDRs
    assert r1[23] is True and r1[24] is True                          # rising tape: above 8/21, near high
    i = sl.prior_index([d for d, _ in conn.series["ACME"]], D(2024, 6, 3))
    assert conn.series["ACME"][i][0] == r1[6]
    assert abs(r1[21] - (sl.ret([c for _, c in conn.series["ACME"]], i, 21)
                         - sl.ret([c for _, c in conn.series["SPY"]], i, 21))) < 1e-12   # stk_rs_etf21
    assert abs(r1[19] - sl.ret([c for _, c in conn.series["ACME"]], i, 21)) < 1e-12      # stk_ret21
    r2 = rows[2]
    assert r2[19] is None and r2[20] is None and r2[24] is None       # warmup: holes, never zeros
    assert r2[6] == D(2024, 1, 4)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
