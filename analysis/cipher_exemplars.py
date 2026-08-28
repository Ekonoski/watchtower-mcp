"""
The cipher exemplar logger (2026-08-27). Eric's eye, captured one
labeled chart at a time.

Why: two mechanical-cipher attempts have now proven the standing rule —
the eye is not specified by adjectives (BW-3D died in one evening; the
cipher_reversal screen took four same-evening calibration passes and is
still a screen, not an entry). The path to a mechanical entry is a
LABELED EXEMPLAR SET: every chart Eric would take, and every near-miss
he refuses, recorded with the system's complete oscillator state at
that moment. At ~30 takes / ~20 passes the boundary between them is a
classification cut on data, not an argument.

Mechanics:
  - `log_exemplar(ticker, timeframe, label, note)` — called by the
    watchtower_log_cipher MCP tool, so it works from any Claude session
    on any device the moment the chart is on screen.
  - The state is copied from oscillator_scan — the system's own stored
    read (bar-stamped, refreshed hourly intraday), never a screenshot
    and never a refetch. No stored row, or a stale one, records a HOLE
    with the reason; the label is kept either way (the eye's verdict is
    data even when the snapshot missed).
  - Forward returns grade every exemplar later via the stored price
    history; takes vs passes get compared when the set is big enough.
  - Writes only cipher_exemplars — pinned by signature in
    tests/test_cipher_exemplars.py.
"""
import datetime as dt
import json
import logging

log = logging.getLogger("watchtower.cipher_exemplars")

TIMEFRAMES = {"1h": "1h", "4h": "4h", "1d": "daily", "d": "daily",
              "daily": "daily", "w": "weekly", "1w": "weekly",
              "weekly": "weekly", "16d": "16d", "16": "16d"}
LABELS = {"take", "pass"}
# Staleness bars, same spirit as the screens: intraday states age fast.
# 16d bars complete every ~16 trading days; 25 calendar days of grace.
STALE_DAYS = {"1h": 1, "4h": 2, "daily": 4, "weekly": 8, "16d": 25}

OSC_COLS = ("bar_ts", "wt1", "wt2", "wt_diff", "mf_candle", "mf_volume",
            "rsi", "stoch_k", "stoch_d", "pctr", "pctr_ema", "macd",
            "macd_signal", "macd_hist", "bars_since_cross", "signals",
            "confluence_score", "direction", "scanned_at")


def normalize(ticker: str, timeframe: str, label: str):
    """Pure. Returns (ticker, timeframe, label) or raises ValueError
    with a message the tool passes straight back to the human."""
    tk = (ticker or "").strip().upper()
    if not tk or len(tk) > 6 or not tk.replace(".", "").isalnum():
        raise ValueError(f"'{ticker}' doesn't look like a ticker")
    tf = TIMEFRAMES.get((timeframe or "").strip().lower())
    if tf is None:
        raise ValueError(f"timeframe '{timeframe}' — use 1h, 4h, daily, "
                         f"or weekly")
    lb = (label or "").strip().lower()
    if lb not in LABELS:
        raise ValueError(f"label '{label}' — use 'take' or 'pass'")
    return tk, tf, lb


def staleness(tf: str, scanned_at, now=None) -> str | None:
    """Pure. None when fresh enough; else the hole reason."""
    if scanned_at is None:
        return "no scanned_at on the stored row"
    now = now or dt.datetime.now(dt.timezone.utc)
    if scanned_at.tzinfo is None:
        scanned_at = scanned_at.replace(tzinfo=dt.timezone.utc)
    age = (now - scanned_at).days
    if age > STALE_DAYS[tf]:
        return (f"stored {tf} state is {age}d old "
                f"(bar cadence allows {STALE_DAYS[tf]}d)")
    return None


def log_exemplar(ticker: str, timeframe: str, label: str,
                 note: str = "", source: str = "live") -> str:
    """Capture one labeled exemplar. Returns a one-line human summary —
    what was captured, or what hole was recorded and why."""
    from screen.reversal_screen import _conn
    tk, tf, lb = normalize(ticker, timeframe, label)
    if tf == "16d":
        # The GOAT timeframe (2026-08-29): no scanned row exists — the
        # state computes on demand from the same fixed-anchor bars the
        # green-dot study graded, so exemplar and study speak one
        # dialect.
        return _log_16d(tk, lb, note, source)
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute(f"""SELECT {', '.join(OSC_COLS)}
                          FROM oscillator_scan
                          WHERE ticker=%s AND timeframe=%s""", (tk, tf))
            row = c.fetchone()
        osc = state = bar_ts = None
        hole = None
        if row is None:
            hole = f"no stored {tf} oscillator row for {tk}"
        else:
            d = dict(zip(OSC_COLS, row))
            hole = staleness(tf, d.get("scanned_at"))
            bar_ts = d.get("bar_ts")
            d["scanned_at"] = str(d["scanned_at"])
            osc = json.dumps(d, default=str)
        state = "hole" if hole else "captured"
        if hole:
            note = (note + " | " if note else "") + f"HOLE: {hole}"
        with conn.cursor() as c:
            c.execute("""SELECT close, trade_date FROM daily_prices
                         WHERE ticker=%s AND close IS NOT NULL
                         ORDER BY trade_date DESC LIMIT 1""", (tk,))
            px = c.fetchone()
        with conn.cursor() as c:
            c.execute("""INSERT INTO cipher_exemplars
                         (ticker, timeframe, label, note, source, state,
                          osc, bar_ts, price, price_asof)
                         VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                         RETURNING id""",
                      (tk, tf, lb, note or None, source, state, osc, bar_ts,
                       px[0] if px else None, px[1] if px else None))
            eid = c.fetchone()[0]
        conn.commit()
        if state == "hole":
            return (f"#{eid} {tk} {tf} {lb.upper()} logged — but the state "
                    f"is a HOLE ({hole}). The label is kept; the snapshot "
                    f"isn't usable for the definition.")
        d = json.loads(osc)
        return (f"#{eid} {tk} {tf} {lb.upper()} captured — bar {bar_ts}: "
                f"MF {d.get('mf_candle')}, waves {d.get('wt1')}/{d.get('wt2')}, "
                f"RSI {d.get('rsi')}, stoch {d.get('stoch_k')}/{d.get('stoch_d')}, "
                f"%R {d.get('pctr')}, MACDh {d.get('macd_hist')}"
                + (f" · \"{note}\"" if note else ""))
    finally:
        conn.close()


def _log_16d(tk: str, lb: str, note: str, source: str) -> str:
    """16d capture path: state from greendot_screen.state_16d (fixed-
    anchor bars, live oscillator engine). A thin history is a NAMED
    hole; the label is kept either way."""
    import json as _json
    from analysis.greendot_screen import state_16d
    from screen.reversal_screen import _conn
    state, bar_or_reason = state_16d(tk)
    hole = None if state is not None else bar_or_reason
    if hole:
        note = (note + " | " if note else "") + f"HOLE: {hole}"
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT close, trade_date FROM daily_prices
                         WHERE ticker=%s AND close IS NOT NULL
                         ORDER BY trade_date DESC LIMIT 1""", (tk,))
            px = c.fetchone()
        with conn.cursor() as c:
            c.execute("""INSERT INTO cipher_exemplars
                         (ticker, timeframe, label, note, source, state,
                          osc, bar_ts, price, price_asof)
                         VALUES (%s,'16d',%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                         RETURNING id""",
                      (tk, lb, note or None, source,
                       "hole" if hole else "captured",
                       _json.dumps(state) if state else None,
                       bar_or_reason if state else None,
                       px[0] if px else None, px[1] if px else None))
            eid = c.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    if hole:
        return (f"#{eid} {tk} 16d {lb.upper()} logged — but the state is "
                f"a HOLE ({hole}). Label kept.")
    return (f"#{eid} {tk} 16d {lb.upper()} captured — bar {bar_or_reason}: "
            f"MF {state.get('mf_candle')}, waves {state.get('wt1')}/"
            f"{state.get('wt2')}, RSI {state.get('rsi')}, "
            f"%R {state.get('pctr')}, MACDh {state.get('macd_hist')}"
            + (f" · \"{note}\"" if note else ""))


def exemplar_summary() -> str:
    """The museum's census: counts by label/state and the latest few."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT label, state, count(*)
                         FROM cipher_exemplars GROUP BY label, state""")
            counts = c.fetchall()
            c.execute("""SELECT id, ticker, timeframe, label, state,
                                logged_at::date
                         FROM cipher_exemplars
                         ORDER BY id DESC LIMIT 8""")
            recent = c.fetchall()
        if not counts:
            return ("No exemplars yet. Log with: ticker, timeframe "
                    "(1h/4h/daily/weekly), take|pass, optional note. "
                    "Target ~30 takes / ~20 passes.")
        head = " · ".join(f"{lb} {st}: {n}" for lb, st, n in sorted(counts))
        lines = [f"Exemplars — {head}"]
        for eid, tk, tf, lb, st, d in recent:
            lines.append(f"  #{eid} {tk} {tf} {lb}"
                         + (" (hole)" if st == "hole" else "") + f" · {d}")
        return "\n".join(lines)
    finally:
        conn.close()
