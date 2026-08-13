"""
Watchtower Imbalances — fair value gaps and inversions.

A displacement candle can move so fast that candle #1's high and candle
#3's low never overlap: the zone between them traded one way only.
Doctrine (per the FVG study card): a gap is a LEVEL WITH EDGES —
respected it acts as support/resistance; closed through, it INVERTS
(polarity flip) and the retest of the dead zone is the failed-reclaim
entry with a sharper stop (acceptance beyond the far edge).

House rules encoded here:
  displacement-quality only   the middle candle must be a conviction
                              candle — body >= 1.5x the trailing median
  wick vs close               a wick through the zone consumes it
                              (filled); only a CLOSE through inverts it
  freshness                   stale inversions (30+ bars) stop mattering;
                              newest gaps sort first
Timeframe-agnostic: pass any OHLC bar list, oldest first.
"""


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def detect_fvgs(bars: list, max_gaps: int = 8,
                min_body_ratio: float = 1.5) -> list:
    """Open + recently-inverted gaps from OHLC dicts (oldest first).
    Returns [{side, status, top, bottom, mid, age_bars}] newest-first."""
    n = len(bars)
    if n < 10:
        return []
    bodies = [abs((b.get("close") or 0) - (b.get("open") or 0)) for b in bars]
    raw = []
    for i in range(2, n):
        med = _median(bodies[max(0, i - 21):i - 1])
        if not med or bodies[i - 1] < min_body_ratio * med:
            continue    # middle candle isn't a displacement — noise, not imbalance
        if bars[i]["low"] > bars[i - 2]["high"]:
            raw.append({"side": "bullish", "top": bars[i]["low"],
                        "bottom": bars[i - 2]["high"], "born": i - 1})
        if bars[i]["high"] < bars[i - 2]["low"]:
            raw.append({"side": "bearish", "top": bars[i - 2]["low"],
                        "bottom": bars[i]["high"], "born": i - 1})
    out = []
    for g in raw:
        status, inverted_at = "open", None
        for j in range(g["born"] + 2, n):
            b = bars[j]
            if g["side"] == "bullish":
                if b["close"] < g["bottom"]:
                    status, inverted_at = "inverted", j
                    break
                if b["low"] <= g["bottom"]:
                    status = "filled"
                    break
            else:
                if b["close"] > g["top"]:
                    status, inverted_at = "inverted", j
                    break
                if b["high"] >= g["top"]:
                    status = "filled"
                    break
        if status == "filled":
            continue
        if status == "inverted" and (n - 1 - inverted_at) > 30:
            continue
        born_bar = bars[g["born"]]
        inv_bar = bars[inverted_at] if inverted_at is not None else None
        out.append({
            "side": g["side"], "status": status,
            "top": round(float(g["top"]), 2),
            "bottom": round(float(g["bottom"]), 2),
            "mid": round((float(g["top"]) + float(g["bottom"])) / 2, 2),
            "age_bars": n - 1 - g["born"],
            # Stamp per row (house rule): a zone without its formation date
            # sends the reader hunting the whole chart for the candles.
            "formed": born_bar.get("date"),
            "formed_session": born_bar.get("session"),
            "inverted_on": inv_bar.get("date") if inv_bar else None,
        })
    out.sort(key=lambda g: g["age_bars"])
    return out[:max_gaps]


# ── Persisted snapshot: the record any session can read (2026-08-13) ─────────
# The Aug 13 board shipped its Imbalances section as a declared hole: zones
# were computed per-request in the dashboard and never persisted, so a
# session without the live engine had nothing to read. Every other board
# input already solved this with persistence (gex_levels, pattern_scan,
# paper_spec_bars); the FVG engine now does the same — a morning sweep from
# OUR recorded daily bars into fvg_runs / fvg_zones, absence disambiguated
# by run rows (n_zones=0 is a recorded quiet read; no run row is a hole).

def _bar_dicts(rows):
    """daily_prices tuples (trade_date, open, high, low, close), oldest
    first, -> the OHLC dicts detect_fvgs reads. Pure and pinned by test —
    a field-order swap across this seam fabricates zones from real bars
    (the paper_spec_bars lesson, applied before it bites)."""
    return [{"date": d.isoformat(), "open": float(o), "high": float(h),
             "low": float(l), "close": float(c)}
            for d, o, h, l, c in rows]


def fvg_universe(venue, watchlist_tickers, open_position_tickers):
    """Pure. Who gets a morning zone sweep: the gamma venues, the active
    watchlist, and every ticker the paper desk is currently holding (an
    open position without its imbalance map is flying blind). Sorted so
    runs are deterministic and diffs read cleanly."""
    return sorted({*venue, *watchlist_tickers, *open_position_tickers})


def _zone_rows(run_id, zones):
    """Pure. detect_fvgs dicts -> fvg_zones value tuples, every field
    carried — direction, status, edges, age, and the formation date the
    reading doctrine demands per row."""
    return [(run_id, z["side"], z["status"], z["top"], z["bottom"],
             z["mid"], z["age_bars"], z.get("formed"), z.get("inverted_on"))
            for z in zones]


def _ensure_fvg_tables(conn):
    """Idempotent, committed on its own (the osc_state pattern): the sweep
    must not depend on the migration having reached the live database."""
    with conn.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS fvg_runs (
            id bigserial PRIMARY KEY, ticker text NOT NULL,
            timeframe text NOT NULL DEFAULT 'daily',
            bars_through date NOT NULL, n_bars int NOT NULL,
            n_zones int NOT NULL,
            computed_at timestamptz NOT NULL DEFAULT now())""")
        c.execute("""CREATE TABLE IF NOT EXISTS fvg_zones (
            run_id bigint NOT NULL REFERENCES fvg_runs(id),
            side text NOT NULL, status text NOT NULL,
            top numeric NOT NULL, bottom numeric NOT NULL,
            mid numeric NOT NULL, age_bars int NOT NULL,
            formed date, inverted_on date)""")
        c.execute("ALTER TABLE fvg_runs ENABLE ROW LEVEL SECURITY")
        c.execute("ALTER TABLE fvg_zones ENABLE ROW LEVEL SECURITY")
    conn.commit()


FVG_MIN_BARS = 60      # thinner than this and a zone read is noise; the
                       # run row still writes, with its n_bars visible
FVG_BAR_WINDOW = 220   # ~10 months of dailies; inversion staleness (30
                       # bars) and gap caps live in detect_fvgs itself


def write_fvg_snapshot():
    """Morning sweep (7:35 ET): daily-timeframe zones for the whole
    universe, from recorded daily_prices bars only — reconstruction is not
    tape, and these bars are the tape we keep. Every ticker gets a run row
    whether or not zones exist; sweep failures per ticker log loudly and
    skip, they never fake an empty read."""
    import datetime as _dt
    import logging
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from screen.reversal_screen import _conn as get_db_connection
    log = logging.getLogger("watchtower.fvg")

    conn = get_db_connection()
    try:
        _ensure_fvg_tables(conn)
        with conn.cursor() as c:
            c.execute("SELECT ticker FROM watchlist WHERE active = true")
            wl = [r[0] for r in c.fetchall()]
            c.execute("""SELECT DISTINCT s.ticker
                         FROM paper_specs s JOIN paper_trades t ON t.spec_id = s.id
                         WHERE t.exited_at IS NULL""")
            held = [r[0] for r in c.fetchall()]
        universe = fvg_universe(("SPY", "QQQ", "IWM", "DIA"), wl, held)
        if not universe:
            log.warning("[fvg] snapshot universe is EMPTY — no runs written; "
                        "that absence will read as a hole, as it should")
            return 0
        n_zones_total = 0
        for tk in universe:
            try:
                with conn.cursor() as c:
                    c.execute("""SELECT trade_date, open, high, low, close
                                 FROM daily_prices
                                 WHERE ticker=%s AND open IS NOT NULL
                                   AND high IS NOT NULL AND low IS NOT NULL
                                   AND close IS NOT NULL
                                 ORDER BY trade_date DESC LIMIT %s""",
                              (tk, FVG_BAR_WINDOW))
                    rows = c.fetchall()[::-1]
                bars = _bar_dicts(rows)
                zones = detect_fvgs(bars) if len(bars) >= FVG_MIN_BARS else []
                if len(bars) < FVG_MIN_BARS:
                    log.warning("[fvg] %s: only %d full-OHLC bars on record "
                                "— run written with zero zones and its thin "
                                "n_bars visible", tk, len(bars))
                if not bars:
                    log.warning("[fvg] %s: NO full-OHLC daily bars — no run "
                                "written (a run needs a bars_through stamp)", tk)
                    continue
                with conn.cursor() as c:
                    c.execute("""INSERT INTO fvg_runs
                                 (ticker, bars_through, n_bars, n_zones)
                                 VALUES (%s,%s,%s,%s) RETURNING id""",
                              (tk, bars[-1]["date"], len(bars), len(zones)))
                    run_id = c.fetchone()[0]
                    if zones:
                        c.executemany("""INSERT INTO fvg_zones
                            (run_id, side, status, top, bottom, mid,
                             age_bars, formed, inverted_on)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            _zone_rows(run_id, zones))
                conn.commit()
                n_zones_total += len(zones)
            except Exception:
                conn.rollback()
                log.exception("[fvg] sweep failed for %s — skipped, the "
                              "record has a hole for it today", tk)
        log.info("[fvg] snapshot: %d tickers swept, %d zones",
                 len(universe), n_zones_total)
        return n_zones_total
    finally:
        conn.close()
