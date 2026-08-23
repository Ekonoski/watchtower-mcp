"""
The day-bias audition book (2026-08-23, Eric: "run the paper audition
for the SPY late-retest play").

What the study graded (SPY 2005-2026, era-stable; QQQ replicates in
direction): on days SPY OPENS above the prior day's high, a retest of
PDH arriving AT/AFTER 10:30 ET wins ~69% to the close with MFE ~2x MAE
(+27bps avg); the pre-10:30 flush is chop and the trade does not exist
there. This book trades EXACTLY that definition, one spec per day,
SPY only, measurement only:

  - 9:46 ET: yesterday's high (PDH) from daily_prices; if the 9:30 bar
    OPENED above PDH the spec arms, else the day records 'skipped_bias'
    (zero is data — the stand-aside is the decision).
  - A resting limit at PDH goes live at 10:30. A touch BEFORE 10:30
    cancels the spec ('cancelled_early') — the graded cell is the
    retest that arrives after the morning proved the level; buying the
    early flush would be trading the coin-flip bucket on purpose.
  - Exit is the TRUE daily close (eod_flat) — the study's outcome
    anchor. One deviation from the graded definition, declared: a
    disaster stop 0.75% below entry, decided on 15m CLOSES only (wick
    rule), guards the tail the EOD-only backtest rode through. R is
    computed against that stop; the book's honest scoreboard is bps.
  - Fills, cancels, and stops decide on recorded completed 15m bars
    (persisted to paper_spec_bars) — never on quotes, never refetched.

Promotion gate: ~30 resolved days, then the 0-2 DTE options expression
question opens per the ledger-grades-the-signal rule. Until then it is
paper, underlying bps, beside the swing and gamma books.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.day_bias")

BOOK = "day_bias"
TICKER = "SPY"
SETUP = "late_retest_pdh"
LATE_START = dt.time(10, 30)
DISASTER_STOP_PCT = 0.0075
ET = None  # set lazily from paper_trader to keep one timezone source


def decide(bars, pdh, stop_pct=DISASTER_STOP_PCT):
    """Pure day evaluator. bars: completed RTH 15m tuples
    (ts_et, open, close, high, low, volume) oldest-first, as
    paper_trader._last_closed_15m returns. Returns the day's state:

      no_bias          - 9:30 bar opened at/below PDH; day not in play
      waiting          - in play, nothing decided yet
      cancelled_early  - PDH touched before 10:30 (graded chop bucket)
      filled           - limit filled at PDH on a >=10:30 touch
      stopped          - post-fill 15m CLOSE through the disaster stop
      no_retest        - caller declares at EOD if still 'waiting'

    A wick through the stop is not a stop (close decides). The fill
    price is PDH — a resting limit's execution fact."""
    if not bars:
        return {"state": "waiting"}
    first = bars[0]
    if first[0].time() != dt.time(9, 30):
        return {"state": "waiting", "hole": "first bar is not 9:30"}
    if float(first[1]) <= pdh:
        return {"state": "no_bias", "open_px": float(first[1])}
    filled = None
    stop = pdh * (1 - stop_pct)
    for (ts, _o, c, _h, lo, *_v) in bars:
        lo, c = float(lo), float(c)
        if filled is None:
            if lo <= pdh:
                if ts.time() < LATE_START:
                    return {"state": "cancelled_early", "at": ts}
                filled = {"state": "filled", "at": ts, "entry": pdh,
                          "stop": stop}
        else:
            if c <= stop:
                return {**filled, "state": "stopped", "stop_at": ts,
                        "stop_px": c}
    return filled or {"state": "waiting"}


def _pdh(conn, today):
    with conn.cursor() as cur:
        cur.execute("""SELECT high FROM daily_prices
                       WHERE ticker=%s AND trade_date < %s
                         AND high IS NOT NULL
                       ORDER BY trade_date DESC LIMIT 1""",
                    (TICKER, today))
        r = cur.fetchone()
    return float(r[0]) if r else None


def run_daybias_loop():
    """5-minute market-hours pass: persist SPY bars, arm/skip the day's
    spec, apply decide(). Idempotent — every transition re-derives from
    recorded state."""
    from analysis.paper_trader import (ET as _ET, _last_closed_15m,
                                       _persist_spec_bars, _rth,
                                       get_db_connection)
    now = dt.datetime.now(_ET)
    if now.weekday() >= 5 or now.time() < dt.time(9, 46):
        return
    today = now.date()
    conn = get_db_connection()
    try:
        bars_all = _last_closed_15m(TICKER)
        if bars_all:
            # Persist EVERY bar seen (premarket included, house rule);
            # only RTH bars decide.
            _persist_spec_bars(conn, TICKER, today, bars_all)
        bars = _rth(bars_all)
        with conn.cursor() as c:
            c.execute("""SELECT id, status, entry_trigger, stop
                         FROM paper_specs
                         WHERE book=%s AND trade_date=%s""", (BOOK, today))
            spec = c.fetchone()
        if spec is None:
            pdh = _pdh(conn, today)
            if pdh is None or not bars:
                return   # daily row or first bar not recorded yet
            res = decide(bars, pdh)
            if res.get("hole"):
                log.warning(f"[day-bias] {today}: {res['hole']} — day "
                            f"recorded as hole, no spec.")
                return
            status = "skipped_bias" if res["state"] == "no_bias" else "armed"
            rationale = (f"{SETUP}: PDH {pdh:g}, 9:30 open "
                         f"{float(bars[0][1]):g} "
                         f"{'<= PDH — stand-aside' if status != 'armed' else '> PDH — long bias'}; "
                         f"limit live only >=10:30 (early touch cancels); "
                         f"exit true close; disaster stop 0.75% on 15m closes. "
                         f"Study: 69% win, +27bps, MFE~2xMAE (n=273, era-stable)")
            with conn.cursor() as c:
                c.execute("""INSERT INTO paper_specs
                    (trade_date, book, ticker, direction, setup,
                     entry_trigger, stop, target, r_dollars, status,
                     rationale, source)
                    VALUES (%s,%s,%s,'long',%s,%s,%s,%s,100,%s,%s,'day_bias_study')
                    ON CONFLICT DO NOTHING""",
                    (today, BOOK, TICKER, SETUP, pdh,
                     round(pdh * (1 - DISASTER_STOP_PCT), 4),
                     round(pdh * 1.0027, 4), status, rationale))
            conn.commit()
            log.info(f"[day-bias] {today}: spec {status} (PDH {pdh:g})")
            if status != "armed":
                return
            spec = None
            with conn.cursor() as c:
                c.execute("""SELECT id, status, entry_trigger, stop
                             FROM paper_specs
                             WHERE book=%s AND trade_date=%s""",
                          (BOOK, today))
                spec = c.fetchone()
        sid, status, trig, stop = spec[0], spec[1], float(spec[2]), float(spec[3])
        if status not in ("armed", "triggered") or not bars:
            return
        res = decide(bars, trig)
        if status == "armed":
            if res["state"] == "cancelled_early":
                with conn.cursor() as c:
                    c.execute("UPDATE paper_specs SET status='cancelled', "
                              "rationale = rationale || ' | cancelled: PDH "
                              "touched before 10:30 (graded chop bucket)' "
                              "WHERE id=%s", (sid,))
                conn.commit()
                log.info(f"[day-bias] {today}: cancelled_early at {res['at']}")
            elif res["state"] in ("filled", "stopped"):
                with conn.cursor() as c:
                    c.execute("""INSERT INTO paper_trades
                        (spec_id, entered_at, entry_px, fill_kind,
                         confirm_status)
                        VALUES (%s,%s,%s,'touch','n/a')""",
                        (sid, res["at"], res["entry"]))
                    c.execute("UPDATE paper_specs SET status='triggered' "
                              "WHERE id=%s", (sid,))
                conn.commit()
                log.info(f"[day-bias] ENTER {TICKER} @ {res['entry']:g} "
                         f"({res['at']})")
                status = "triggered"
        if status == "triggered" and res["state"] == "stopped":
            risk = res["entry"] - res["stop"]
            r = (res["stop_px"] - res["entry"]) / risk if risk > 0 else None
            with conn.cursor() as c:
                c.execute("""UPDATE paper_trades SET exited_at=%s,
                             exit_px=%s, exit_reason='stop', r_multiple=%s
                             WHERE spec_id=%s AND exited_at IS NULL""",
                          (res["stop_at"], res["stop_px"], r, sid))
            conn.commit()
            log.info(f"[day-bias] STOP {TICKER} @ {res['stop_px']:g}")
    finally:
        conn.close()


def run_daybias_settle():
    """16:42 ET: exit the day's open trade at the TRUE daily close
    (close_sync lands 16:35); cancel unfilled armed specs as no_retest.
    A missing daily row falls back to the recorded 15:45 bar's close,
    else logs a hole and leaves the trade for the next pass."""
    from analysis.paper_trader import ET as _ET, get_db_connection
    now = dt.datetime.now(_ET)
    if now.weekday() >= 5:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT t.id, t.entry_px, s.stop, s.trade_date
                         FROM paper_trades t
                         JOIN paper_specs s ON s.id = t.spec_id
                         WHERE s.book=%s AND t.exited_at IS NULL""", (BOOK,))
            open_trades = c.fetchall()
        for tid, entry, stop, tdate in open_trades:
            entry, stop = float(entry), float(stop)
            with conn.cursor() as c:
                c.execute("""SELECT close FROM daily_prices
                             WHERE ticker=%s AND trade_date=%s""",
                          (TICKER, tdate))
                r = c.fetchone()
                close = float(r[0]) if r and r[0] is not None else None
                if close is None:
                    c.execute("""SELECT close FROM paper_spec_bars
                                 WHERE ticker=%s AND trade_date=%s
                                 ORDER BY ts DESC LIMIT 1""",
                              (TICKER, tdate))
                    r = c.fetchone()
                    close = float(r[0]) if r else None
            if close is None:
                log.warning(f"[day-bias] settle {tdate}: no close recorded "
                            f"— hole, retrying next pass.")
                continue
            risk = entry - stop
            rm = (close - entry) / risk if risk > 0 else None
            with conn.cursor() as c:
                c.execute("""UPDATE paper_trades SET exited_at=now(),
                             exit_px=%s, exit_reason='eod_flat',
                             r_multiple=%s WHERE id=%s""", (close, rm, tid))
            conn.commit()
            log.info(f"[day-bias] EOD {TICKER} @ {close:g} "
                     f"({(close-entry)/entry*10000:+.1f}bps)")
        with conn.cursor() as c:
            c.execute("""UPDATE paper_specs SET status='cancelled',
                         rationale = rationale || ' | no retest by close'
                         WHERE book=%s AND status='armed'
                           AND trade_date <= CURRENT_DATE""", (BOOK,))
        conn.commit()
    finally:
        conn.close()
