"""
The full-stack dot study (2026-08-29 night, Eric: "I don't take every
green dot... I am looking for names where momentum, RSI, Williams %R,
MACD have bottomed and are turning to the upside. I use all of them"
— and, same conversation: "I look for support or demand zone with a
deep pullback and multiple indicators starting to turn bullish").

Grades Eric's ACTUAL entry against the raw-dot baseline: every
recorded 16D dot gets its state legs tagged at the dot bar, from the
same fixed-anchor bars and the LIVE oscillator engine. Legs, frozen:

  pctr_turn  Williams %R floored recently (<= -80 within the last 3
             16D bars incl. the dot bar) AND rising at the dot.
  rsi_turn   RSI depressed (<= 50) AND rising at the dot.
  macd_turn  MACD line below zero AND histogram rising (red shrinking
             or fresh green — "below the zero line and curving up").
  mf         money-flow value stored raw; cuts at readout.
  shelf_days LOCATION PROXY, stated as one: count of days in the
             prior 504 whose close sits within +/-3% of the dot close
             — time-at-price standing in for a demand shelf. The ACHR
             trendline case motivates it; a real levels-engine replay
             is the upgrade if the proxy grades.

Readout: each leg's solo lift over the plain-dot baseline, then
combinations including the full stack (Eric's entry), deep-cohort
and era cuts, WITH the missed-runner accounting — the filter pays
for what it skips, same bar the doji faced. Writes only
greendot_stack; resume by ticker; marker greendot_stack_v1.
"""
import logging

log = logging.getLogger("watchtower.greendot_stack")

COMPLETE_MARKER = "greendot_stack_v1"
PCTR_FLOOR = -80.0
PCTR_FLOOR_BARS = 3
RSI_CEIL = 50.0
SHELF_BAND = 0.03
SHELF_LOOKBACK = 504


def stack_legs(cur, prev, pctr_recent_min):
    """Pure. cur/prev: dicts with pctr, rsi, macd, macd_hist (None-able).
    pctr_recent_min: min %R over the last PCTR_FLOOR_BARS bars incl.
    the dot bar. Returns dict of leg booleans (False on any hole)."""
    def ok(*vals):
        return all(v is not None for v in vals)
    pctr_turn = (ok(cur.get("pctr"), prev.get("pctr"), pctr_recent_min)
                 and pctr_recent_min <= PCTR_FLOOR
                 and cur["pctr"] > prev["pctr"])
    rsi_turn = (ok(cur.get("rsi"), prev.get("rsi"))
                and cur["rsi"] <= RSI_CEIL and cur["rsi"] > prev["rsi"])
    macd_turn = (ok(cur.get("macd"), cur.get("macd_hist"),
                    prev.get("macd_hist"))
                 and cur["macd"] < 0
                 and cur["macd_hist"] > prev["macd_hist"])
    return {"pctr_turn": pctr_turn, "rsi_turn": rsi_turn,
            "macd_turn": macd_turn}


def shelf_days(closes, di, px):
    """Pure. Location proxy: days in the prior SHELF_LOOKBACK whose
    close sits within +/-SHELF_BAND of px."""
    lo, hi = px * (1 - SHELF_BAND), px * (1 + SHELF_BAND)
    seg = closes[max(0, di - SHELF_LOOKBACK):di]
    return sum(1 for c in seg if lo <= c <= hi)


def _process_ticker(conn, tk, cal):
    import math
    import pandas as pd
    from analysis.greendot_study import blocks_16d
    from analysis.oscillator import compute_oscillator
    with conn.cursor() as c:
        c.execute("""SELECT id, dot_date FROM greendot_dots
                     WHERE ticker=%s ORDER BY dot_date""", (tk,))
        dots = c.fetchall()
        c.execute("""SELECT trade_date, COALESCE(open, close),
                            COALESCE(high, close), COALESCE(low, close),
                            close, COALESCE(volume, 0)
                     FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (tk,))
        rows = c.fetchall()
    if not dots or len(rows) < 200:
        return
    dates = [r[0] for r in rows]
    closes = [float(r[4]) for r in rows]
    blk = blocks_16d(dates, cal)
    bars, cur, cur_id = [], None, None
    for i, r in enumerate(rows):
        if blk[i] != cur_id:
            if cur is not None:
                bars.append(cur)
            cur_id = blk[i]
            cur = dict(end=dates[i], o=float(r[1]), h=float(r[2]),
                       l=float(r[3]), c=float(r[4]), v=float(r[5]), di=i)
        else:
            cur.update(h=max(cur["h"], float(r[2])),
                       l=min(cur["l"], float(r[3])), c=float(r[4]),
                       v=cur["v"] + float(r[5]), end=dates[i], di=i)
    if len(bars) < 40:
        return
    df = pd.DataFrame({k: [b[x] for b in bars] for k, x in
                       (("open", "o"), ("high", "h"), ("low", "l"),
                        ("close", "c"), ("volume", "v"))},
                      index=pd.DatetimeIndex(
                          [pd.Timestamp(b["end"]) for b in bars]))
    odf = compute_oscillator(df)

    def f(row, k):
        try:
            v = float(row.get(k))
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None

    end_to_bi = {b["end"]: j for j, b in enumerate(bars)}
    for did, d0 in dots:
        bi = end_to_bi.get(d0)
        if bi is None or bi < 1:
            continue
        cur_row = {k: f(odf.iloc[bi], k)
                   for k in ("pctr", "rsi", "macd", "macd_hist",
                             "mf_candle")}
        prev_row = {k: f(odf.iloc[bi - 1], k)
                    for k in ("pctr", "rsi", "macd", "macd_hist")}
        recent = [f(odf.iloc[j], "pctr")
                  for j in range(max(0, bi - PCTR_FLOOR_BARS + 1), bi + 1)]
        recent = [v for v in recent if v is not None]
        pmin = min(recent) if recent else None
        legs = stack_legs(cur_row, prev_row, pmin)
        di = bars[bi]["di"]
        sd = shelf_days(closes, di, closes[di])
        with conn.cursor() as c:
            c.execute("""INSERT INTO greendot_stack
                (dot_id, pctr, pctr_turn, rsi, rsi_turn, macd_line,
                 macd_turn, mf, shelf_days)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                (did,
                 round(cur_row["pctr"], 2) if cur_row["pctr"] is not None else None,
                 legs["pctr_turn"],
                 round(cur_row["rsi"], 2) if cur_row["rsi"] is not None else None,
                 legs["rsi_turn"],
                 round(cur_row["macd"], 4) if cur_row["macd"] is not None else None,
                 legs["macd_turn"],
                 round(cur_row["mf_candle"], 2) if cur_row["mf_candle"] is not None else None,
                 sd))
    conn.commit()


def run(batch: int = 400) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT DISTINCT g.ticker FROM greendot_dots g
                         WHERE NOT EXISTS (SELECT 1 FROM greendot_stack s
                                           WHERE s.dot_id = g.id)
                         ORDER BY g.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                          "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                          (COMPLETE_MARKER,))
            conn.commit()
            log.info("[greendot-stack] complete.")
            return True
        for tk in todo:
            try:
                _process_ticker(conn, tk, cal)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-stack] %s failed: %s", tk, str(e)[:300])
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_stack (dot_id, note)
                                 SELECT id, 'ticker_error'
                                 FROM greendot_dots WHERE ticker=%s
                                 ON CONFLICT DO NOTHING""", (tk,))
                conn.commit()
        log.info("[greendot-stack] processed %d ticker(s).", len(todo))
        return False
    finally:
        conn.close()
