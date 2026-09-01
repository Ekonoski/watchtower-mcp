"""
The RS-leader audition book (2026-08-31 late — Eric: "Yes go build the
paper book"). One spec per day, trading EXACTLY the graded definition
alongside Eric's own manual execution of the same signal — the book
grades the rule, the journal grades the hand, and the gap between them
is the human-interference measurement he asked for.

The graded definition (rsleader_study + hybrid_exit_study, all
definitions IMPORTED, never reimplemented):
  - 9:45 ET: rank the mag 7 by return-from-open minus QQQ's; the
    leader qualifies at rank 1 with RS >= +0.4%. No qualifier ->
    'skipped_rank' (zero is data). Laggard shorts are NOT traded
    (refused, era flip).
  - Entry: the FIRST 1m bar 9:45-11:00 that touches the 1m 8/21 EMA
    and CLOSES holding (wick rule), at that close. No GO by 11:00 ->
    'cancelled' (no_qualifier — a recorded decision).
  - Stop: under the pullback bar (-0.05%), exits on a completed 5m
    CLOSE through; disaster cap -1% from entry on TOUCH.
  - At entry +1R (1m high touch): the trail — exit on a 5m CLOSE
    below the day-anchored 5m 21 EMA (the hybrid study's frame).
  - Survivors exit at the close (eod_flat).
  Prior beside every spec: +0.40/+0.27 avg R by year-half, ~40% win,
  n=377 leader days; caveats — closes as fills, no costs.

Fill honesty: every decision reads bars PERSISTED to rsl_book_bars
(1m, written as first seen, never revised — reconstruction is not
tape); the tick loop is idempotent and re-derives all state from the
record, so restarts change nothing. Writes ONLY rsl_book_bars and
book='rs_leader' rows in paper_specs/paper_trades. Promotion gate:
~30 resolved trades, small-n rule beside every number until then.
"""
import datetime as dt
import logging

from analysis.hybrid_exit_study import _ema as ema5
from analysis.hybrid_exit_study import _res5 as res5
from analysis.rsleader_study import (ENTRY_CUTOFF, MEASURE, RS_MIN, TICKERS,
                                     ema, find_go_entry, rs_rank)

log = logging.getLogger("watchtower.rsl_book")

BOOK = "rs_leader"
SETUP = "rsl_go_trail"
DISASTER_PCT = 0.01
EOD = dt.time(15, 59)


def lifecycle_state(bars, i_go, entry, stop):
    """Pure: the live trade's state from persisted 1m bars after the
    GO bar. Returns {'armed': bool, 'exit': (reason, ts, px) | None}.
    Wick rule: stop/trail decide on completed 5m closes; only the
    disaster cap exits on touch."""
    risk = entry - stop
    arm_px = entry + risk
    disaster = entry * (1 - DISASTER_PCT)
    bars5, last5 = res5(bars)
    e21_5 = ema5([b[4] for b in bars5], 21)
    e21_by_min = {last5[j]: e21_5[j] for j in range(len(bars5))}
    armed = False
    for i in range(i_go + 1, len(bars)):
        ts, o, h, l, c = bars[i]
        if l <= disaster:
            return {"armed": armed, "exit": ("disaster", ts, disaster)}
        if h >= arm_px:
            armed = True
        e21 = e21_by_min.get(i)
        if e21 is not None:
            if armed and c < e21:
                return {"armed": True, "exit": ("trail", ts, c)}
            if not armed and c < stop:
                return {"armed": False, "exit": ("stop", ts, c)}
    return {"armed": armed, "exit": None}


def _persist_1m(conn, ticker, today):
    """Fetch today's 1m bars and persist NEW ones (first-seen wins);
    return the persisted series oldest-first."""
    from zoneinfo import ZoneInfo
    from analysis.polygon_data import get_client
    et = ZoneInfo("America/New_York")
    client = get_client()
    if client is not None:
        cutoff = dt.datetime.now(et).replace(second=0, microsecond=0)
        try:
            aggs = list(client.get_aggs(ticker, multiplier=1,
                                        timespan="minute",
                                        from_=today.isoformat(),
                                        to=today.isoformat(), limit=1200))
            rows = []
            for a in aggs:
                t = dt.datetime.fromtimestamp(a.timestamp / 1000,
                                              dt.timezone.utc).astimezone(et)
                if dt.time(9, 30) <= t.time() <= EOD and t < cutoff:
                    rows.append((ticker, t, t.date(), float(a.open),
                                 float(a.high), float(a.low),
                                 float(a.close),
                                 float(a.volume) if a.volume is not None
                                 else None))
            if rows:
                with conn.cursor() as c:
                    c.executemany(
                        """INSERT INTO rsl_book_bars
                           (ticker, ts, trade_date, open, high, low,
                            close, volume)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (ticker, ts) DO NOTHING""", rows)
                conn.commit()
        except Exception as e:
            log.warning(f"[rsl-book] {ticker} 1m fetch failed: {e}")
    with conn.cursor() as c:
        c.execute("""SELECT ts, open, high, low, close FROM rsl_book_bars
                     WHERE ticker=%s AND trade_date=%s ORDER BY ts""",
                  (ticker, today))
        return [(ts.astimezone(et), float(o), float(h), float(l), float(cl))
                for ts, o, h, l, cl in c.fetchall()]


def run_rsl_tick():
    """Per-minute pass 9:46-16:01 ET. Idempotent; every transition
    re-derives from rsl_book_bars + the spec/trade rows."""
    from analysis.paper_trader import ET as _ET, get_db_connection
    now = dt.datetime.now(_ET)
    if now.weekday() >= 5 or now.time() < dt.time(9, 46):
        return
    today = now.date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, ticker, status, stop FROM paper_specs
                         WHERE book=%s AND trade_date=%s""", (BOOK, today))
            spec = c.fetchone()

        if spec is None:
            # 9:45 rank from persisted bars, all 8 references
            rets = {}
            for tk in TICKERS + ("QQQ",):
                bars = _persist_1m(conn, tk, today)
                if not bars:
                    return                       # feed hole; retry next tick
                o930 = bars[0][1]
                px = None
                for b in bars:
                    if b[0].time() < MEASURE:
                        px = b[4]
                    else:
                        break
                if px is None:
                    return
                rets[tk] = (px / o930 - 1) * 100
            qqq = rets.pop("QQQ")
            leader, _laggard, _mid, rs = rs_rank(rets, qqq)
            if leader is None:
                with conn.cursor() as c:
                    c.execute("""INSERT INTO paper_specs
                        (trade_date, book, ticker, direction, setup,
                         entry_trigger, stop, target, r_dollars, status,
                         rationale, source)
                        VALUES (%s,%s,'—','long',%s,0,0,0,100,
                                'skipped_rank',%s,'rsleader_study')
                        ON CONFLICT DO NOTHING""",
                        (today, BOOK, SETUP,
                         f"{SETUP}: no name cleared +{RS_MIN}% vs QQQ at "
                         f"9:45 — stand-aside (zero is data). Board: " +
                         ", ".join(f"{t} {rs[t]:+.2f}" for t in
                                   sorted(rs, key=rs.get, reverse=True))))
                conn.commit()
                log.info(f"[rsl-book] {today}: skipped_rank")
                return
            ref = rets[leader]
            with conn.cursor() as c:
                c.execute("""INSERT INTO paper_specs
                    (trade_date, book, ticker, direction, setup,
                     entry_trigger, stop, target, r_dollars, status,
                     rationale, source)
                    VALUES (%s,%s,%s,'long',%s,%s,%s,%s,100,'armed',%s,
                            'rsleader_study')
                    ON CONFLICT DO NOTHING""",
                    (today, BOOK, leader, SETUP, 0, 0, 0,
                     f"{SETUP}: leader {leader} {ref:+.2f}% vs QQQ "
                     f"(bar +{RS_MIN}%). Entry = first 1m 8/21 hold "
                     f"9:45-11:00 at its close; stop under the pullback "
                     f"bar on 5m CLOSES; -1% disaster on touch; trail "
                     f"(5m close < 5m 21EMA) after +1R; eod for "
                     f"survivors. Prior +0.40/+0.27R by half, ~40% win, "
                     f"n=377 (closes as fills, no costs). "
                     f"entry_trigger/stop are 0 until the GO sets them."))
            conn.commit()
            log.info(f"[rsl-book] {today}: armed on {leader}")
            return

        sid, ticker, status, stop_db = spec
        if status in ("skipped_rank", "cancelled"):
            return
        bars = _persist_1m(conn, ticker, today)
        if len(bars) < 16:
            return
        with conn.cursor() as c:
            c.execute("""SELECT id, entered_at, entry_px, exited_at
                         FROM paper_trades WHERE spec_id=%s""", (sid,))
            trade = c.fetchone()

        if trade is None and status == "armed":
            closes = [b[4] for b in bars]
            e8, e21 = ema(closes, 8), ema(closes, 21)
            i945 = next((i for i, b in enumerate(bars)
                         if b[0].time() >= MEASURE), None)
            icut = next((i for i, b in enumerate(bars)
                         if b[0].time() >= ENTRY_CUTOFF), len(bars))
            if i945 is None:
                return
            got = find_go_entry(bars, e8, e21, i945, icut, "long")
            if got is not None:
                i, entry, stop = got
                with conn.cursor() as c:
                    c.execute("""INSERT INTO paper_trades
                        (spec_id, entered_at, entry_px, fill_kind,
                         confirm_status)
                        VALUES (%s,%s,%s,'close','n/a')""",
                        (sid, bars[i][0], round(entry, 4)))
                    c.execute("""UPDATE paper_specs SET status='triggered',
                                 entry_trigger=%s, stop=%s, target=%s
                                 WHERE id=%s""",
                              (round(entry, 4), round(stop, 4),
                               999999, sid))  # sentinel: this book has NO target;
                               # a plausible placeholder is a live number
                               # to every reader (the 2026-09-01 phantom)
                conn.commit()
                log.info(f"[rsl-book] ENTER {ticker} @ {entry:.4f} "
                         f"({bars[i][0]})")
                return
            if now.time() >= ENTRY_CUTOFF:
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_specs SET status='cancelled',
                        rationale = rationale ||
                        ' | no 1m 8/21 hold by 11:00 — no trade (recorded '
                        'decision)' WHERE id=%s""", (sid,))
                conn.commit()
                log.info(f"[rsl-book] {today}: cancelled (no qualifier)")
            return

        if trade is not None and trade[3] is None:
            tid, ent_at, entry_px, _ = trade
            entry_px = float(entry_px)
            stop_lvl = float(stop_db)
            if stop_lvl <= 0:
                return                            # inconsistent row; hole
            i_go = next((i for i, b in enumerate(bars)
                         if b[0] >= ent_at.astimezone(bars[0][0].tzinfo)),
                        None)
            if i_go is None:
                return
            st = lifecycle_state(bars, i_go, entry_px, stop_lvl)
            risk = entry_px - stop_lvl
            if st["exit"] is not None:
                reason, ts, px = st["exit"]
                r = (px - entry_px) / risk if risk > 0 else None
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_trades SET exited_at=%s,
                                 exit_px=%s, exit_reason=%s, r_multiple=%s
                                 WHERE id=%s AND exited_at IS NULL""",
                              (ts, round(px, 4), reason, r, tid))
                conn.commit()
                log.info(f"[rsl-book] EXIT {ticker} {reason} @ {px:.4f}")
            elif now.time() >= dt.time(16, 0) and bars:
                px = bars[-1][4]
                r = (px - entry_px) / risk if risk > 0 else None
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_trades SET exited_at=%s,
                                 exit_px=%s, exit_reason='eod_flat',
                                 r_multiple=%s
                                 WHERE id=%s AND exited_at IS NULL""",
                              (bars[-1][0], round(px, 4), r, tid))
                conn.commit()
                log.info(f"[rsl-book] EOD {ticker} @ {px:.4f}")
    finally:
        conn.close()
