"""
Watchtower — paper-trading platform v1 (the measurement engine).

Three books, never blended (see paper_specs.book):
  gamma      — the playbook's qualified day-trade plays, generated each
               morning from gex_levels. The unproven edge this platform
               exists to measure; the clean control for gamma_iday.
  gamma_iday — the SAME rules applied to the live intraday board
               (gex_intraday) every loop cycle: as walls/flip move, new
               levels arm and abandoned levels cancel. Measures how much
               edge the morning-only book leaves on the table. When both
               boards agree the two books deliberately hold the same trade.
  swing      — breakout-retest limits from pattern_scan, every (pattern,
               timeframe) class with a positive backtest prior (see
               SWING_CLASSES). Already backtested; runs as the control
               group (and as the blind-limit vs stall-entry experiment).

House rules encoded here, not approximated:
  - Wick rule: every trigger and stop is a COMPLETED 15-minute-bar close,
    never a tick. A wick through a level changes nothing.
  - Magnitude rule: gamma specs only when |net GEX| >= 1.0bn (load-bearing).
  - Matrix gates: no directional specs on a collapsed magnet (CW == PW);
    no put-wall dip-buys in v1 (the flip gate makes them rare and they are
    the easiest rule to get subtly wrong — excluded until the ledger earns
    the complexity).
  - Geometry filter: target room must be >= 1.5x stop distance or no spec.
  - Binary gate: NFP / CPI / FOMC / PCE days mark every gamma spec
    skipped_binary at spec time. Other 10:00 ET High-impact releases block
    NEW entries 9:55-10:15 (loop-level).
  - Clock rules (day-trade books): no new entries after 14:30 ET; force-flat
    at 15:55 (exit_reason eod_flat); two stops in one book in one day halts
    that book. The swing book holds overnight — no force-flat, entries
    workable until the close, and its stops accept on the DAILY close per
    the wick rule (a daily pattern is judged on daily bars, not 15m pokes).
  - R is computed against the SPEC's stop distance; fills are the trigger
    bar's close. Slippage lives in the gap between spec R and realized R.
"""
import datetime as dt
import logging
import os
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screen.reversal_screen import _conn as get_db_connection  # noqa: E402
from analysis.polygon_data import fetch_recent_bars  # noqa: E402

log = logging.getLogger("watchtower.paper")
ET = zoneinfo.ZoneInfo("America/New_York")
VENUE = ["SPY", "QQQ", "IWM"]
BINARY_EVENTS = ("Non Farm Payrolls", "CPI", "FOMC", "Interest Rate Decision",
                 "Core PCE")
# The swing book trades every (pattern, timeframe) CLASS with a positive
# backtest prior — and no negative-prior class, ever (the lesson that
# retired shorts). A flat pattern-list × timeframe-list cannot express
# this: ema_bounce is the strongest weekly class on the board and the
# WORST daily one. Priors: pattern_backtest, bullish, avg realized R
# (queried 2026-08-08):
#   asc_triangle   weekly +2.61 (n=51)    daily +0.47 (n=37)
#   ema_bounce     weekly +0.98 (n=205)   daily -0.37 (n=162)  EXCLUDED
#   bull_flag      weekly +0.39 (n=44)    daily +0.19 (n=25)
#   inverse_hs     weekly +0.31 (n=47)    daily +0.27 (n=13)
#   double_bottom  weekly +0.28 (n=60)    daily -0.19 (n=19)   kept*
#   higher_low     weekly +0.27 (n=168)   daily -0.06 (n=49)   kept*
# *The daily neckline classes ride as the entry-location experiment
# (Eric, 2026-08-08): their priors were graded on breakout-CLOSE entries;
# the desk buys the retest at the trigger. If they still grade negative
# after ~30 resolved live trades, they retire the way shorts did.
SWING_CLASSES = (
    ("higher_low", "weekly"), ("higher_low", "daily"),
    ("double_bottom", "weekly"), ("double_bottom", "daily"),
    ("inverse_hs", "weekly"), ("inverse_hs", "daily"),
    ("asc_triangle", "weekly"), ("asc_triangle", "daily"),
    ("bull_flag", "weekly"), ("bull_flag", "daily"),
    ("ema_bounce", "weekly"),
)
SWING_PATTERNS = tuple(dict.fromkeys(p for p, _tf in SWING_CLASSES))
# The swing book is a CURATED control, not the whole scanner. Weekly/daily
# only (the backtested retest claims: weekly higher_low 63%, daily 50% — the
# 4h was never the retest thesis), one spec per ticker, top-N by score. On
# 2026-08-07 the uncurated query armed 151 blind limits on an NFP morning.
SWING_TIMEFRAMES = ("weekly", "daily")
SWING_MAX = 15


def swing_class_ok(pattern: str, timeframe: str) -> bool:
    """The class gate, pure so it pins in a test. The SQL query filters by
    pattern AND timeframe independently; this is the joint filter that
    keeps a pattern's excluded timeframe (ema_bounce daily) out of the
    book even though both its pattern and its timeframe are individually
    tradable."""
    return (pattern, timeframe) in SWING_CLASSES


def curate_swing(rows, cap=SWING_MAX):
    """Pure. rows: (ticker, timeframe, pattern, direction, trigger, target,
    invalid, score) already geometry-filtered. One spec per ticker (weekly
    beats daily, then higher score), top `cap` by score.
    Returns (kept, dropped_count)."""
    best = {}
    for r in rows:
        tk, tf, score = r[0], r[1], r[7]
        rank = (tf == "weekly", score)
        if tk not in best or rank > (best[tk][1] == "weekly", best[tk][7]):
            best[tk] = r
    kept = sorted(best.values(), key=lambda r: -r[7])[:cap]
    return kept, len(rows) - len(kept)


def _touch(level, px):
    return abs(px - level) / level <= 0.001


def build_gamma_specs(trade_date, levels, status="armed", book="gamma"):
    """Playbook rules → paper_specs rows. The single source of truth: the live
    morning spec-writer, the intraday re-armer, and the replay harness
    (analysis/gamma_replay.py) all call this, so a rule change backtests and
    trades identically across every book.

    levels: iterable of (ticker, spot, call_wall, put_wall, gamma_flip,
            net_gex, regime) — the freshest board per ticker.
    Returns (specs, skips): specs are paper_specs value tuples,
    skips are (ticker, reason) so "no spec" is always explainable.
    """
    specs, skips = [], []
    for tk, spot, cw, pw, flip, gex, regime in levels:
        spot, cw, pw = float(spot), float(cw or 0), float(pw or 0)
        flip = float(flip) if flip is not None else None
        gex = float(gex or 0)
        why_skip = None
        if abs(gex) < 1.0:
            why_skip = f"net GEX {gex:+.2f}bn below load-bearing"
        elif cw and pw and cw == pw:
            why_skip = f"collapsed magnet at {cw} — matrix row three"
        if why_skip:
            skips.append((tk, why_skip))
            continue
        # Wall fade (pinning, spot below CW): short the stall at the wall.
        if regime == "pinning" and cw and spot < cw:
            tgt = max(flip or 0, (cw + pw) / 2 if pw else 0)
            stop = round(cw * 1.0015, 2)
            if tgt and (cw - tgt) >= 1.5 * (stop - cw):
                specs.append((trade_date, book, tk, "short", f"wall_fade_{cw:g}",
                              cw, stop, round(tgt, 2), status,
                              f"first-touch fade at {cw:g} CW, {gex:+.1f}bn pinning; "
                              f"entry=15m close back under wall after touch; "
                              f"stop=15m close beyond {stop}; target {tgt:g}"))
        # Flip-hold long (pinning, flip below spot, room to CW).
        if regime == "pinning" and flip and cw and flip < spot < cw:
            stop = round(flip * 0.9985, 2)
            if (cw - flip) >= 1.5 * (flip - stop):
                specs.append((trade_date, book, tk, "long", f"flip_hold_{flip:g}",
                              flip, stop, cw, status,
                              f"flip-hold long at {flip:g} ({gex:+.1f}bn pinning); "
                              f"entry=touch then 15m close back above flip; "
                              f"stop=15m close under {stop}; target CW {cw:g}"))
        # Slippery stack fade: CW and flip within 0.5% = the stack.
        if regime == "slippery" and flip and cw and spot < min(flip, cw) \
                and abs(cw - flip) / flip <= 0.005:
            stack = max(cw, flip)
            stop = round(stack * 1.0015, 2)
            tgt = round(spot - (stack - spot), 2)  # symmetric room, capped by geometry
            if (stack - tgt) >= 1.5 * (stop - stack):
                specs.append((trade_date, book, tk, "short", f"stack_fade_{stack:g}",
                              stack, stop, tgt, status,
                              f"slippery stack fade {stack:g} (CW+flip, {gex:+.1f}bn); "
                              f"counter-trend entry, with-trend hold"))
    return specs, skips


# ── Morning spec-writer (7:40 ET, after the 7:30 gamma sweep) ────────────────

def write_morning_specs():
    today = dt.datetime.now(ET).date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # book-scoped so a restart after the open doesn't mistake the
            # intraday book's rows for an already-written morning batch
            c.execute("""SELECT 1 FROM paper_specs
                         WHERE trade_date=%s AND book != 'gamma_iday' LIMIT 1""", (today,))
            if c.fetchone():
                log.info("[paper] specs already written for %s", today)
                return
            c.execute("""SELECT event FROM economic_calendar
                         WHERE country='US' AND event_date=%s AND impact='High'""", (today,))
            highs = [r[0] for r in c.fetchall()]
        binary_day = any(b.lower() in e.lower() for e in highs for b in BINARY_EVENTS)
        status = "skipped_binary" if binary_day else "armed"

        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT ON (ticker) ticker, spot, call_wall, put_wall,
                                gamma_flip, net_gex, regime
                         FROM gex_levels WHERE ticker = ANY(%s)
                         ORDER BY ticker, computed_at DESC""", (VENUE,))
            specs, skips = build_gamma_specs(today, c.fetchall(), status)
            for tk, why_skip in skips:
                log.info("[paper] %s: no gamma spec — %s", tk, why_skip)

            # Swing book: breakout-retest limits (blind by design — control group).
            c.execute("""SELECT ticker, timeframe, pattern, direction, trigger_price,
                                target, invalid_level, score
                         FROM pattern_scan
                         WHERE pattern = ANY(%s) AND status='breakout' AND score >= 70
                           AND direction='bullish' AND timeframe = ANY(%s)
                           AND dist_to_trigger_pct BETWEEN 0 AND 4""",
                      (list(SWING_PATTERNS), list(SWING_TIMEFRAMES)))
            candidates = [(tk, tf, pat, d, float(trig), float(tgt), float(inv), score)
                          for tk, tf, pat, d, trig, tgt, inv, score in c.fetchall()
                          if swing_class_ok(pat, tf)
                          and (float(tgt) - float(trig)) >= 1.5 * (float(trig) - float(inv))]
            kept, dropped = curate_swing(candidates)
            if dropped:
                log.info("[paper] swing: %d qualified, curated to %d (dropped %d)",
                         len(candidates), len(kept), dropped)
            for tk, tf, pat, _dir, trig, tgt, inv, score in kept:
                specs.append((today, "swing", tk, "long", f"retest_{pat}_{tf}",
                              trig, inv, tgt, "armed",
                              f"{pat} {tf} breakout (score {score}); blind limit at the "
                              f"trigger per retest doctrine; stop=pattern invalid {inv:g}"))

        with conn.cursor() as c:
            c.executemany("""INSERT INTO paper_specs
                (trade_date, book, ticker, direction, setup, entry_trigger, stop,
                 target, status, rationale)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", specs)
        conn.commit()
        log.info("[paper] %d specs written for %s (binary_day=%s: %s)",
                 len(specs), today, binary_day, ", ".join(highs) or "none")
    finally:
        conn.close()


# ── Intraday re-armer: same rules, live board (gex_intraday) ─────────────────

IDAY_FRESH_MIN = 25   # intraday sweep cadence is ~15 min; older = unavailable
IDAY_LAST_NEW = dt.time(14, 30)   # matches the loop's no-new-entries clock


def diff_intraday_specs(existing, fresh):
    """Pure. existing: [(id, ticker, setup, status)] — today's gamma_iday rows.
    fresh: spec tuples from build_gamma_specs off the live board.

    Returns (to_insert, to_cancel):
      to_insert — fresh specs whose (ticker, setup) has NOT existed today in
        any status. One shot per level per day: a level that armed, cancelled,
        and came back does not re-arm (prevents flip-flop churn at a contested
        level, and keeps one row per level so the ledger reads cleanly).
      to_cancel — armed spec ids whose level is absent from the live board
        (the board moved; the level is no longer the playbook's trade).
        Triggered specs are never cancelled — open trades manage to exit.
    """
    seen = {(tk, setup) for _id, tk, setup, _st in existing}
    live = {(sp[2], sp[4]) for sp in fresh}
    to_insert = [sp for sp in fresh if (sp[2], sp[4]) not in seen]
    to_cancel = [_id for _id, tk, setup, st in existing
                 if st == "armed" and (tk, setup) not in live]
    return to_insert, to_cancel


def write_intraday_specs(conn):
    """Arm gamma_iday specs from the freshest gex_intraday board. Runs at the
    top of every trigger-loop cycle; every gate build_gamma_specs applies to
    the morning book (magnitude, collapsed magnet, geometry) applies here
    identically, plus the same binary-day gate."""
    now = dt.datetime.now(ET)
    if not (dt.time(9, 35) <= now.time() < IDAY_LAST_NEW):
        return
    today = now.date()
    with conn.cursor() as c:
        c.execute("""SELECT event FROM economic_calendar
                     WHERE country='US' AND event_date=%s AND impact='High'""", (today,))
        if any(b.lower() in e.lower() for (e,) in c.fetchall() for b in BINARY_EVENTS):
            return                              # binary day — book sits out
        c.execute("""SELECT DISTINCT ON (ticker) ticker, spot, call_wall, put_wall,
                            gamma_flip, net_gex, regime
                     FROM gex_intraday
                     WHERE ticker = ANY(%s) AND ts > now() - %s * interval '1 minute'
                     ORDER BY ticker, ts DESC""", (VENUE, IDAY_FRESH_MIN))
        board = c.fetchall()
    if not board:
        log.warning("[paper] gamma_iday: no fresh intraday board (>%dmin stale) "
                    "— arming nothing, existing specs untouched", IDAY_FRESH_MIN)
        return
    fresh, _skips = build_gamma_specs(today, board, "armed", book="gamma_iday")
    with conn.cursor() as c:
        c.execute("""SELECT id, ticker, setup, status FROM paper_specs
                     WHERE trade_date=%s AND book='gamma_iday'""", (today,))
        to_insert, to_cancel = diff_intraday_specs(c.fetchall(), fresh)
        if to_insert:
            c.executemany("""INSERT INTO paper_specs
                (trade_date, book, ticker, direction, setup, entry_trigger, stop,
                 target, status, rationale)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", to_insert)
        for sid in to_cancel:
            c.execute("""UPDATE paper_specs SET status='cancelled',
                         rationale = rationale || ' | cancelled: board moved off level'
                         WHERE id=%s""", (sid,))
    conn.commit()
    if to_insert or to_cancel:
        log.info("[paper] gamma_iday: +%d armed, %d cancelled (board move)",
                 len(to_insert), len(to_cancel))


# ── Intraday trigger loop (every 5 min, 9:35–15:55 ET) ───────────────────────

def _last_closed_15m(tk):
    """Completed 15m bars today, oldest→newest:
    [(ts_et, open, close, high, low)]. The open matters: honest fill
    pricing on gap bars needs it (see _swing_fill)."""
    bars = fetch_recent_bars(tk, days=2, multiplier=15, timespan="minute")
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for b in bars:
        ts = b.get("timestamp")
        t = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc) if ts else None
        if t and t + dt.timedelta(minutes=15) <= now:   # completed only
            te = t.astimezone(ET)
            if te.date() == dt.datetime.now(ET).date():
                out.append((te, float(b["open"]), float(b["close"]),
                            float(b["high"]), float(b["low"])))
    return out


def _rth(bars: list) -> list:
    """Regular-session bars only: start ≥ 9:30 ET and completing by 16:00
    (bar start ≤ 15:45). Decided 2026-08-08 (Eric): premarket moves are
    low-volume fakeouts — the desk waits for open-market volume. The tape
    that forced the rule: MOS's 9:15–9:30 premarket bar dipped to 23.26 and
    touched a 23.3951 limit the regular session never came back to confirm
    (day closed 23.06). Every bar is still PERSISTED (paper_spec_bars) —
    premarket is recorded, never decisive — so the counterfactual stays
    measurable from stored tape."""
    return [b for b in bars
            if dt.time(9, 30) <= b[0].time() <= dt.time(15, 45)]


def _swing_fill(direction: str, trig: float, stop: float, live_bars: list):
    """Resting-limit fill for the swing book, honestly priced.

    2026-08-08 shadow audit: fills booked blindly at the trigger created
    phantoms on BOTH sides — ARW "filled" at 220.87 on a day whose high was
    209.60 (phantom loss), and TNDM "filled" at 18.16 after opening 4.2%
    below it. One declared, symmetric model (Eric's reclaim rule):

    - RETEST SIDE (price on the pattern's side of the level): a limit at
      trig fills on a touch, at trig — the retest working as speced.
    - LEVEL LOST (bar opens through the trigger): if the open is already
      beyond the STOP, the setup is dead on arrival — cancelled, never
      entered. Otherwise the spec enters RECLAIM mode: a lost level is
      only bought back on proof, and a wick is not proof — the first
      completed 15m bar CLOSING back through the trigger fills, at that
      bar's close. Wick-throughs that fade back fill nothing; below the
      level, nothing ever fills — no knife-catching at opens.

    Every fill price is a price that traded; the same rule prunes losers
    (ARW, BLND) and winners' flattery alike.

    live_bars: [(ts, open, close, high, low)] post-spec-creation only.
    Returns ("fill", px, kind) | ("doa", None, None) | (None, None, None),
    where kind is 'touch' or 'reclaim' — the ledger records which mechanism
    filled, so touch fills can carry their confirmation shadow (see
    _confirm_shadow) and audits never have to infer kind from the price.
    """
    sign = 1 if direction == "long" else -1
    lost = False
    for _, bop, bc2, bhi, blo in live_bars:
        opened_beyond = (bop < trig) if direction == "long" else (bop > trig)
        if not lost and not opened_beyond:
            # Price is on the retest side: a limit at trig fills on a touch.
            touched = (blo <= trig) if direction == "long" else (bhi >= trig)
            if touched:
                return ("fill", trig, "touch")
            continue
        if not lost:
            # The level was opened through — lost. If the open is already
            # beyond the STOP the setup died before it could act: cancel.
            if sign * (bop - stop) <= 0:
                return ("doa", None, None)
            lost = True
        # RECLAIM (Eric, 2026-08-08, v2 of his rule): a lost level is only
        # bought back on PROOF, and per the wick rule a wick through the
        # trigger is not proof. The first completed 15m bar CLOSING back
        # through the trigger fills — at that bar's close, a printed
        # price; the premium over the trigger is the cost of confirmation.
        # A wick over that fades back is exactly the fakeout this refuses.
        # Once lost, the spec stays in reclaim mode (a later gap back over
        # the level still fills on its close — proof is the close, however
        # price got there).
        if sign * (bc2 - trig) >= 0:
            return ("fill", bc2, "reclaim")
    return (None, None, None)


def _confirm_shadow(direction: str, trig: float, live_bars: list):
    """The confirmation shadow (Eric, 2026-08-08): the swing book keeps
    resting-limit fills at the trigger, but every touch fill also records
    what a confirmation-gated desk would have done with the same spec —
    entry at the first completed 15m bar CLOSING back through the trigger
    AFTER the touch, at that bar's close. The touch bar itself counts if
    its own close is back through. Bars before the touch never count: for
    a long, every pre-dip bar closes above the trigger — that is price
    sitting above the level, not proof it held.

    Pure so it backtests. live_bars: [(ts, open, close, high, low)],
    post-spec-creation. Returns (px, ts) or None (no confirming close —
    the confirmation desk never takes the trade the limit owns).
    """
    sign = 1 if direction == "long" else -1
    touched = False
    for ts, _bop, bc2, bhi, blo in live_bars:
        if not touched:
            touched = (blo <= trig) if direction == "long" else (bhi >= trig)
            if not touched:
                continue
        if sign * (bc2 - trig) >= 0:
            return (bc2, ts)
    return None


def _spec_bar_rows(tk: str, trade_date, bars: list) -> list:
    """Map _last_closed_15m tuples (ts, open, close, high, low) to
    paper_spec_bars rows (ticker, ts, open, high, low, close, trade_date).
    Pure and pinned by test — the (close, high, low) reordering across this
    seam is exactly the kind of silent field-swap that killed the trigger
    loop on day one."""
    return [(tk, ts, op, hi, lo, cl, trade_date)
            for ts, op, cl, hi, lo in bars]


def _persist_spec_bars(conn, tk, trade_date, bars):
    """Reconstruction is not tape (TNDM, 2026-08-08): audits must replay
    from bars the loop actually decided on, not refetched history. Runs
    every pass, idempotent — a mid-day crash keeps everything seen so far."""
    rows = _spec_bar_rows(tk, trade_date, bars)
    if not rows:
        return
    with conn.cursor() as c:
        c.executemany("""INSERT INTO paper_spec_bars
                         (ticker, ts, open, high, low, close, trade_date)
                         VALUES (%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ticker, ts) DO NOTHING""", rows)
    conn.commit()


def backfill_spec_bars(trade_date) -> int:
    """One-shot per date: fetch the 15m session bars for every ticker that
    had a spec on `trade_date` and persist them to paper_spec_bars. Exists
    for 2026-08-07 — the shadow audit's reclaim entries were priced off
    reconstruction, and the retraction rule is now: reprice from recorded
    tape or not at all. Polygon keeps intraday history, the deployed
    service holds the key; this runs where both exist. Idempotent: skips
    entirely once the date has any stored bars, so it cannot fight the
    live loop's own persistence (which covers every day from 2026-08-10)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM paper_spec_bars WHERE trade_date=%s LIMIT 1",
                      (trade_date,))
            if c.fetchone():
                return 0
            c.execute("SELECT DISTINCT ticker FROM paper_specs WHERE trade_date=%s",
                      (trade_date,))
            tickers = sorted(r[0] for r in c.fetchall())
        days_back = max(2, (dt.date.today() - trade_date).days + 1)
        total = 0
        for tk in tickers:
            bars = fetch_recent_bars(tk, days=days_back, multiplier=15,
                                     timespan="minute")
            out = []
            for b in bars:
                ts_ms = b.get("timestamp")
                if ts_ms is None:
                    continue
                te = dt.datetime.fromtimestamp(
                    ts_ms / 1000, dt.timezone.utc).astimezone(ET)
                if te.date() == trade_date:
                    out.append((te, float(b["open"]), float(b["close"]),
                                float(b["high"]), float(b["low"])))
            _persist_spec_bars(conn, tk, trade_date, out)
            total += len(out)
        log.info("[paper] spec-bar backfill %s: %d bars across %d tickers",
                 trade_date, total, len(tickers))
        return total
    finally:
        conn.close()


def _set_confirm(conn, tid, px, ts, status):
    with conn.cursor() as c:
        c.execute("""UPDATE paper_trades SET confirm_px=%s, confirm_at=%s,
                     confirm_status=%s WHERE id=%s""", (px, ts, status, tid))
    conn.commit()


def run_trigger_loop():
    now = dt.datetime.now(ET)
    if now.weekday() >= 5 or not (dt.time(9, 35) <= now.time() <= dt.time(15, 58)):
        return
    today = now.date()
    conn = get_db_connection()
    try:
        try:
            write_intraday_specs(conn)   # arm from the live board, then evaluate
        except Exception:
            conn.rollback()
            log.exception("[paper] gamma_iday arming failed — managing existing specs")
        with conn.cursor() as c:
            c.execute("""SELECT s.id, s.book, s.ticker, s.direction, s.setup,
                                s.entry_trigger, s.stop, s.target, s.status,
                                s.created_at, t.id, t.entry_px, t.entered_at, t.exited_at,
                                t.confirm_status
                         FROM paper_specs s LEFT JOIN paper_trades t ON t.spec_id=s.id
                         WHERE (s.trade_date=%s AND s.status IN ('armed','triggered'))
                            OR (s.status='triggered' AND t.id IS NOT NULL
                                AND t.exited_at IS NULL)""",
                      (today,))
            rows = c.fetchall()
        if not rows:
            return
        # two-stop halt, per book (stops that HAPPENED today, whatever day
        # the spec was written — overnight swing stops count too)
        with conn.cursor() as c:
            c.execute("""SELECT s.book, count(*) FROM paper_trades t
                         JOIN paper_specs s ON s.id=t.spec_id
                         WHERE t.exit_reason='stop' AND t.exited_at::date=%s
                         GROUP BY s.book""", (today,))
            halted = {b for b, n in c.fetchall() if n >= 2}
        eod = now.time() >= dt.time(15, 55)
        no_new = eod or now.time() >= dt.time(14, 30)

        for (sid, book, tk, direction, setup, trig, stop, tgt, status,
             created_at, tid, entry_px, entered_at, exited, confirm_status) in rows:
            trig, stop, tgt = float(trig), float(stop), float(tgt)
            if exited:
                continue
            bars = _last_closed_15m(tk)
            if not bars:
                continue
            try:
                _persist_spec_bars(conn, tk, today, bars)
            except Exception:
                conn.rollback()
                log.exception("[paper] spec-bar persist failed for %s — "
                              "loop continues, the record has a hole", tk)
            # Persist everything seen; DECIDE only on regular-session bars.
            bars = _rth(bars)
            if not bars:
                continue
            ts, op_, close, hi, lo = bars[-1]
            sign = 1 if direction == "long" else -1
            if tid is None:                          # not entered yet
                # 14:30 no-new is a day-trade clock; a swing resting limit
                # stays workable until the close (its spec cancels at eod).
                if book in halted or (eod if book == "swing" else no_new):
                    if eod:
                        _cancel(conn, sid, "day over")
                    continue
                # A touch only counts on bars ending after the spec existed —
                # an intraday-armed level doesn't inherit the morning's tape.
                live_bars = [b for b in bars
                             if b[0] + dt.timedelta(minutes=15) > created_at]
                entry_fill, kind = None, None
                if book == "swing":
                    verdict, px, kind = _swing_fill(direction, trig, stop, live_bars)
                    if verdict == "doa":
                        _cancel(conn, sid, "gapped past stop — dead on arrival, no fill")
                        continue
                    entered, entry_fill = (verdict == "fill"), px
                else:
                    touched = any((lo2 <= trig <= hi2) or _touch(trig, c2)
                                  for _, _, c2, hi2, lo2 in live_bars)
                    entered = touched and (sign * (close - trig) > 0)  # 15m close back through
                    entry_fill, kind = close, "close_through"
                if entered:
                    # Confirmation shadow: only a touch fill has an open
                    # question — reclaim and gamma entries already ARE
                    # confirmed closes ('n/a', shadow equals actual).
                    cpx = cts = None
                    if kind == "touch":
                        cstat = "pending"
                        shadow = _confirm_shadow(direction, trig, live_bars)
                        if shadow:
                            cpx, cts, cstat = shadow[0], shadow[1], "confirmed"
                    else:
                        cstat = "n/a"
                    with conn.cursor() as c:
                        c.execute("""INSERT INTO paper_trades (spec_id, entered_at, entry_px,
                                     fill_kind, confirm_px, confirm_at, confirm_status)
                                     VALUES (%s, now(), %s, %s, %s, %s, %s)""",
                                  (sid, entry_fill, kind, cpx, cts, cstat))
                        c.execute("UPDATE paper_specs SET status='triggered' WHERE id=%s", (sid,))
                    conn.commit()
                    log.info("[paper] ENTER %s %s %s @ %.2f (%s, %s, shadow=%s)",
                             book, tk, direction, entry_fill, setup, kind, cstat)
            else:                                    # open — manage exit
                entry_px = float(entry_px)
                # Resolve a pending confirmation shadow. Same-day only: the
                # confirmation desk either entered by the close or skipped
                # (unfilled specs cancel at eod). If the loop lost the entry
                # day (restart, outage), the answer is UNKNOWN — 'unresolved'
                # renders as a data hole, never as a zero.
                if confirm_status == "pending":
                    if entered_at is not None and \
                            entered_at.astimezone(ET).date() != today:
                        _set_confirm(conn, tid, None, None, "unresolved")
                    else:
                        live_bars = [b for b in bars
                                     if b[0] + dt.timedelta(minutes=15) > created_at]
                        shadow = _confirm_shadow(direction, trig, live_bars)
                        if shadow:
                            _set_confirm(conn, tid, shadow[0], shadow[1], "confirmed")
                        elif eod:
                            _set_confirm(conn, tid, None, None, "no_confirm")
                # R risk from the ACTUAL entry, not the spec trigger — a gap
                # fill below the trigger carries less risk per share, and
                # grading it off the trigger would misstate every R after it.
                r_dist = abs(entry_px - stop) or 0.01
                # Entry fills at the entry bar's close, so that bar's range is
                # pre-entry price action — grading its high/low as a fill would
                # be lookahead. Stop/target only count on bars ending after entry.
                post_entry = entered_at is None or ts + dt.timedelta(minutes=15) > entered_at
                exit_px, reason = None, None
                if book == "swing":
                    # Multi-day hold: no force-flat, and per the wick rule a
                    # daily-pattern stop accepts on the DAILY close (approx.
                    # the final 15m bar), never an intraday poke.
                    if eod and post_entry and sign * (close - stop) < 0:
                        exit_px, reason = close, "stop"
                    elif post_entry and ((direction == "long" and hi >= tgt)
                                         or (direction == "short" and lo <= tgt)):
                        exit_px, reason = tgt, "target"
                elif post_entry and sign * (close - stop) < 0:  # 15m close beyond stop
                    exit_px, reason = close, "stop"
                elif post_entry and ((direction == "long" and hi >= tgt)
                                     or (direction == "short" and lo <= tgt)):
                    exit_px, reason = tgt, "target"
                elif eod:
                    exit_px, reason = close, "eod_flat"
                if exit_px is not None:
                    r_mult = round(sign * (exit_px - entry_px) / r_dist, 2)
                    with conn.cursor() as c:
                        c.execute("""UPDATE paper_trades SET exited_at=now(), exit_px=%s,
                                     exit_reason=%s, r_multiple=%s WHERE id=%s""",
                                  (exit_px, reason, r_mult, tid))
                    conn.commit()
                    log.info("[paper] EXIT %s %s %s @ %.2f (%s, %+.2fR)",
                             book, tk, direction, exit_px, reason, r_mult)
    finally:
        conn.close()


def _cancel(conn, sid, note):
    with conn.cursor() as c:
        c.execute("UPDATE paper_specs SET status='cancelled', "
                  "rationale = rationale || ' | cancelled: ' || %s WHERE id=%s", (note, sid))
    conn.commit()
