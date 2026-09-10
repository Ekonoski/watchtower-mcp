"""
The swing book's MAE-vs-stop read (2026-09-10 — Eric: "run the MAE read
tonight"). The rs_leader readout left a desk rule: an R unit is a CLAIM
about where the trade fails — grade the claim before grading the trade
in R. The swing book is 5-28 with every stop printing worse than −1R;
this asks whether those stops sat where the trades actually failed, or
where the normal shakeout prints.

SPEC (frozen before any number):
  population  every swing-book fill (paper_trades × paper_specs,
              book in swing / swing_v2 — the v2 book grades here too,
              its `setup` and book name keeping the cohorts apart),
              resolved AND open. Verdicts come from
              RESOLVED trades only; open trades carry a running
              excursion, reported apart and never in the verdict.
  tape        paper_spec_bars — the trade's OWN recorded 15m bars, RTH
              only (paper_trader._rth, one definition), from the fill
              bar through the exit bar (open trades: the last recorded
              bar). The fill bar is included: its low may have printed
              before or after the fill and a 15m bar cannot order that
              — stated. daily_prices_clean supplies ATR14 at entry (the
              14 completed days BEFORE the fill date, no lookahead) and
              the post-exit path. Nothing is refetched.
  unit        R = entry − stop from the ACTUAL fill (the book's unit).
  excursion   mae_touch_r  worst 15m LOW vs entry, in R, through exit
              mae_close_r  worst 15m CLOSE vs entry, in R
              mae_daily_r  worst OFFICIAL daily close vs entry, in R,
                           over the held days (daily_prices_clean — the
                           vendor's close; the ledger settles on the
                           recorded 15:45 bar, and the two differ on
                           thin names: AGMB 13.03 vs 13.22. Stated.)
              mfe_r        best 15m HIGH vs entry before the exit
              mfe_pre_mae_r best high BEFORE the MAE bar
              t_mae_days / t_exit_days  trading days from the fill date
              touched_stop_first  a 15m LOW touched the stop before any
                           exit (winners that survived a touch; losers
                           that were touched before the close took them)
  stop shape  stop_pct = risk / entry; stop_atr = risk / ATR14.
  post-exit   stopped trades only, daily_prices_clean, the 20 trading
              days AFTER the exit date: post_max_r (max high vs the
              ORIGINAL entry, in R), post_min_r, reclaim_day (first
              daily close ≥ entry, 1-based, else NULL), target_touched
              (a daily high ≥ the original target), post_days (how many
              of the 20 exist — fewer than 20 is a hole).
  verdict     stopped:  shakeout — a daily close back ≥ entry within
                        the 20 post days;
                        failure  — none, with all 20 days on record;
                        pending  — none yet, fewer than 20 on record
                                   (a hole, never a failure).
              won:      won_clean / won_after_touch by touched_stop_first.
              open:     open (running excursion only).
  counterfactual (secondary, declared): the SAME entries and target,
              the book's own exit rule — stop on the DAILY close, target
              on a HIGH touch — re-run with the stop at 1.5× and 2.0×
              the original distance and at 1.0 ATR14, capped at 40
              trading days after the fill. Decision sequence, per day:
              the recorded 15m bars (target touches; the fill day's
              post-fill bars only) then ONE close bar carrying the
              official daily close (fill day: high/low from the post-
              fill 15m bars — the daily high would be lookahead); after
              the live exit, daily bars only. Unresolved at the record's
              end = hole. R on the ORIGINAL unit so variants compare on
              one scale. Variant x1_0 replays the live stop through the
              same code — `live_match` says whether it reproduces the
              ledger's exit, a free audit of the replay, and
              `phantom_stop` names a ledger stop whose exit-day OFFICIAL
              close held at/above the stop (2026-09-10: the loop's eod
              branch fired on the 15:30–15:45 bar — six of 28).
  readout     n=33 resolved: no both-halves bar exists at this n. The
              read is descriptive, n beside every number, per class
              small-n. NO rule changes — the per-class ~30 gates decide;
              this says what those reviews should look at.

Writes ONLY swing_mae_events (one row per trade, upserted each pass so
pending post-exit windows and open trades keep grading). No completion
marker: rides the 16:47 daily pass beside target_shadow plus boot.
"""
import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

log = logging.getLogger("watchtower.swing_mae")

ET = ZoneInfo("America/New_York")
POST_DAYS = 20
CF_CAP_DAYS = 40
ATR_N = 14
CF_VARIANTS = ("x1_0", "x1_5", "x2_0", "atr1")
FINAL_BAR_START = dt.time(15, 45)


# ── pure cores ───────────────────────────────────────────────────────

def atr14(daily, fill_date, n=ATR_N):
    """Pure. daily = [(date, o, h, l, c)] ascending. ATR over the n
    completed days BEFORE fill_date (simple mean of true ranges). Fewer
    than n+1 prior bars → None (a hole)."""
    prior = [d for d in daily if d[0] < fill_date]
    if len(prior) < n + 1:
        return None
    trs = []
    for i in range(len(prior) - n, len(prior)):
        _, _, h, l, c = prior[i]
        pc = prior[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def excursion(bars, entry, stop, risk, daily_closes=()):
    """Pure. bars = post-fill 15m RTH bars [(ts_et, o, h, l, c)] through
    the exit bar; daily_closes = the official closes of the held days.
    Returns the excursion dict (R on the given risk)."""
    if not bars or risk <= 0:
        return None
    mae = 0.0
    mae_i = 0
    mae_close = 0.0
    mae_daily = min([0.0] + [(c - entry) / risk for c in daily_closes])
    mfe = 0.0
    mfe_pre = 0.0
    touched = None
    for i, (ts, o, h, l, c) in enumerate(bars):
        adv = (l - entry) / risk
        if adv < mae:
            mae, mae_i = adv, i
        mae_close = min(mae_close, (c - entry) / risk)
        fav = (h - entry) / risk
        mfe = max(mfe, fav)
        if touched is None and l <= stop:
            touched = i
    for ts, o, h, l, c in bars[:mae_i]:
        mfe_pre = max(mfe_pre, (h - entry) / risk)
    days = sorted({b[0].date() for b in bars})
    mae_day = bars[mae_i][0].date()
    return {"mae_touch_r": round(mae, 3), "mae_close_r": round(mae_close, 3),
            "mae_daily_r": round(mae_daily, 3), "mfe_r": round(mfe, 3),
            "mfe_pre_mae_r": round(mfe_pre, 3),
            "t_mae_days": days.index(mae_day), "n_days": len(days),
            "touched_stop_first": touched is not None}


def post_path(post, entry, target, risk):
    """Pure. post = the daily bars AFTER the exit date, ascending, at most
    POST_DAYS of them [(date, o, h, l, c)]. R vs the ORIGINAL entry."""
    if risk <= 0:
        return None
    out = {"post_days": len(post), "post_max_r": None, "post_min_r": None,
           "reclaim_day": None, "target_touched": None}
    if not post:
        return out
    out["post_max_r"] = round(max((b[2] - entry) / risk for b in post), 3)
    out["post_min_r"] = round(min((b[3] - entry) / risk for b in post), 3)
    out["target_touched"] = any(b[2] >= target for b in post)
    for i, b in enumerate(post):
        if b[4] >= entry:
            out["reclaim_day"] = i + 1
            break
    return out


def verdict(exit_reason, exc, post):
    """Pure. The pre-registered classification."""
    if exit_reason is None:
        return "open"
    if exit_reason == "target":
        return "won_after_touch" if exc and exc["touched_stop_first"] else "won_clean"
    if exit_reason != "stop" or post is None:
        return "other"
    if post["reclaim_day"] is not None:
        return "shakeout"
    return "failure" if post["post_days"] >= POST_DAYS else "pending"


def replay(seq, entry, stop, target, cap_days=CF_CAP_DAYS):
    """Pure. The book's own swing exit rule on a decision sequence:
    seq = [(date, h, l, c, is_close_bar)] — 15m bars carry is_close_bar
    only on the 15:45 bar, daily bars always. Stop on a CLOSE-bar close
    beyond the stop (wick rule), target on a HIGH touch, stop precedence
    when both print in one bar (as swing_settle_decision). Cap: exits at
    the last bar's close on the cap day ('cap'); a sequence that ends
    before resolving is a 'hole'. R on the ORIGINAL risk passed in by
    the caller (this returns the exit price; the caller scales)."""
    if not seq:
        return {"outcome": "hole", "exit_px": None, "days": 0}
    d0 = seq[0][0]
    days = sorted({s[0] for s in seq})
    for d, h, l, c, eod in seq:
        nd = days.index(d)
        if eod and c < stop:
            return {"outcome": "stop", "exit_px": c, "days": nd}
        if h >= target:
            return {"outcome": "target", "exit_px": target, "days": nd}
        if nd >= cap_days and eod:
            return {"outcome": "cap", "exit_px": c, "days": nd}
    return {"outcome": "hole", "exit_px": None, "days": len(days) - 1}


def cf_stops(entry, stop, atr):
    risk = entry - stop
    out = {"x1_0": stop, "x1_5": entry - 1.5 * risk, "x2_0": entry - 2.0 * risk,
           "atr1": (entry - atr) if atr else None}
    return out


# ── the pass ─────────────────────────────────────────────────────────

def _load(conn):
    with conn.cursor() as c:
        c.execute("""SELECT t.id, s.id, s.ticker, s.setup, t.entered_at, t.entry_px,
                            s.stop, s.target, t.exited_at, t.exit_px, t.exit_reason,
                            t.r_multiple
                     FROM paper_trades t JOIN paper_specs s ON s.id = t.spec_id
                     WHERE s.book IN ('swing', 'swing_v2') AND s.direction = 'long'
                     ORDER BY t.id""")
        return c.fetchall()


def _spec_bars(conn, ticker, d0, d1):
    from analysis.paper_trader import _rth
    with conn.cursor() as c:
        c.execute("""SELECT ts, open, high, low, close FROM paper_spec_bars
                     WHERE ticker = %s AND trade_date BETWEEN %s AND %s ORDER BY ts""",
                  (ticker, d0, d1))
        rows = [(ts.astimezone(ET), float(o), float(h), float(l), float(cl))
                for ts, o, h, l, cl in c.fetchall()]
    return _rth(rows)


def _daily(conn, ticker, d0, d1):
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, COALESCE(open, close), COALESCE(high, close),
                            COALESCE(low, close), close
                     FROM daily_prices_clean
                     WHERE ticker = %s AND trade_date BETWEEN %s AND %s AND close IS NOT NULL
                     ORDER BY trade_date""", (ticker, d0, d1))
        return [(d, float(o), float(h), float(l), float(cl)) for d, o, h, l, cl in c.fetchall()]


def grade_trade(row, spec_bars, daily, today):
    """Pure given its inputs. Returns the swing_mae_events row dict."""
    (tid, sid, tk, setup, entered_at, entry, stop, target, exited_at,
     exit_px, exit_reason, live_r) = row
    entry, stop, target = float(entry), float(stop), float(target)
    risk = entry - stop
    fill_date = entered_at.astimezone(ET).date()
    exit_date = exited_at.astimezone(ET).date() if exited_at else None
    end_date = exit_date or today
    # post-fill bars: the fill bar itself counts (stated in the spec);
    # the live loop's lookahead guard (bar end > entered_at) is applied.
    post_fill = [b for b in spec_bars
                 if fill_date <= b[0].date() <= end_date
                 and b[0] + dt.timedelta(minutes=15) > entered_at.astimezone(ET)]
    daily_by_date = {d[0]: d for d in daily}
    held = [d for d in daily if fill_date <= d[0] <= end_date]
    exc = excursion(post_fill, entry, stop, risk, [d[4] for d in held])
    atr = atr14(daily, fill_date)
    held_days = [d for d in daily if fill_date < d[0] <= end_date]
    post = None
    if exit_reason == "stop":
        post = post_path([d for d in daily if d[0] > exit_date][:POST_DAYS], entry, target, risk)
    v = verdict(exit_reason, exc, post)
    exit_day_close_r = None
    phantom = None
    if exit_date is not None and exit_date in daily_by_date:
        exit_day_close_r = round((daily_by_date[exit_date][4] - entry) / risk, 3)
        if exit_reason == "stop":
            phantom = daily_by_date[exit_date][4] >= stop
    # counterfactual sequence: per held day the recorded 15m bars (no
    # close-bar flag — the loop never holds the true final bar) then one
    # close bar carrying the OFFICIAL daily close; the fill day's close
    # bar takes its high/low from the post-fill 15m bars (the daily high
    # would be lookahead); after the live exit, daily bars only.
    seq = []
    held_dates = sorted({b[0].date() for b in post_fill} | {d[0] for d in held})
    for d in held_dates:                      # a day the tape missed still closes
        day_bars = [b for b in post_fill if b[0].date() == d]
        seq += [(d, b[2], b[3], b[4], False) for b in day_bars]
        if d in daily_by_date:
            dd = daily_by_date[d]
            if d == fill_date:
                if not day_bars:
                    continue                  # no post-fill tape: the fill day cannot decide
                seq.append((d, max(b[2] for b in day_bars), min(b[3] for b in day_bars), dd[4], True))
            else:
                seq.append((d, dd[2], dd[3], dd[4], True))
    seq += [(d[0], d[2], d[3], d[4], True) for d in daily if d[0] > end_date]
    cf = {}
    stops = cf_stops(entry, stop, atr)
    for name in CF_VARIANTS:
        s = stops.get(name)
        if s is None or risk <= 0:
            cf[name] = None                       # ATR hole → variant hole
            continue
        r = replay(seq, entry, s, target)
        cf[name] = {"outcome": r["outcome"], "days": r["days"],
                    "r": round((r["exit_px"] - entry) / risk, 3) if r["exit_px"] is not None else None}
    live_match = None
    if exit_reason in ("stop", "target") and cf.get("x1_0") and exit_px is not None:
        x = cf["x1_0"]
        live_match = (x["outcome"] == exit_reason
                      and x["r"] is not None
                      and abs(x["r"] - float(live_r)) <= 0.05) if live_r is not None else None
    return {
        "trade_id": tid, "spec_id": sid, "ticker": tk, "setup": setup,
        "entered_at": entered_at, "exited_at": exited_at, "exit_reason": exit_reason,
        "live_r": float(live_r) if live_r is not None else None,
        "entry_px": entry, "stop_px": stop, "target_px": target,
        "stop_pct": round(risk / entry * 100, 3) if entry else None,
        "atr14": round(atr, 4) if atr else None,
        "stop_atr": round(risk / atr, 3) if atr else None,
        "n_bars": len(post_fill),
        **({k: exc[k] for k in ("mae_touch_r", "mae_close_r", "mae_daily_r", "mfe_r",
                                 "mfe_pre_mae_r", "t_mae_days", "touched_stop_first")}
           if exc else {k: None for k in ("mae_touch_r", "mae_close_r", "mae_daily_r", "mfe_r",
                                          "mfe_pre_mae_r", "t_mae_days", "touched_stop_first")}),
        "t_exit_days": len(held_days),
        **({k: post[k] for k in ("post_days", "post_max_r", "post_min_r", "reclaim_day", "target_touched")}
           if post else {"post_days": None, "post_max_r": None, "post_min_r": None,
                         "reclaim_day": None, "target_touched": None}),
        "verdict": v, "live_match": live_match, "cf": cf,
        "exit_day_close_r": exit_day_close_r, "phantom_stop": phantom,
    }


def run() -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    today = dt.datetime.now(ET).date()
    n = 0
    try:
        rows = _load(conn)
        for row in rows:
            tid, sid, tk, setup, entered_at, *_ = row
            exited_at = row[8]
            fill_date = entered_at.astimezone(ET).date()
            end_date = exited_at.astimezone(ET).date() if exited_at else today
            try:
                spec_bars = _spec_bars(conn, tk, fill_date, end_date)
                daily = _daily(conn, tk, fill_date - dt.timedelta(days=60),
                               today)
                g = grade_trade(row, spec_bars, daily, today)
            except Exception:
                conn.rollback()
                log.exception("[swing_mae] trade %s failed to grade", tid)
                continue
            with conn.cursor() as c:
                c.execute("""INSERT INTO swing_mae_events
                    (trade_id, spec_id, ticker, setup, entered_at, exited_at, exit_reason, live_r,
                     entry_px, stop_px, target_px, stop_pct, atr14, stop_atr, n_bars,
                     mae_touch_r, mae_close_r, mae_daily_r, mfe_r, mfe_pre_mae_r, t_mae_days,
                     t_exit_days, touched_stop_first, post_days, post_max_r, post_min_r,
                     reclaim_day, target_touched, verdict, live_match, cf,
                     exit_day_close_r, phantom_stop, graded_at)
                    VALUES (%(trade_id)s, %(spec_id)s, %(ticker)s, %(setup)s, %(entered_at)s,
                            %(exited_at)s, %(exit_reason)s, %(live_r)s, %(entry_px)s, %(stop_px)s,
                            %(target_px)s, %(stop_pct)s, %(atr14)s, %(stop_atr)s, %(n_bars)s,
                            %(mae_touch_r)s, %(mae_close_r)s, %(mae_daily_r)s, %(mfe_r)s,
                            %(mfe_pre_mae_r)s, %(t_mae_days)s, %(t_exit_days)s,
                            %(touched_stop_first)s, %(post_days)s, %(post_max_r)s, %(post_min_r)s,
                            %(reclaim_day)s, %(target_touched)s, %(verdict)s, %(live_match)s,
                            %(cf)s, %(exit_day_close_r)s, %(phantom_stop)s, now())
                    ON CONFLICT (trade_id) DO UPDATE SET
                        exited_at = EXCLUDED.exited_at, exit_reason = EXCLUDED.exit_reason,
                        live_r = EXCLUDED.live_r, n_bars = EXCLUDED.n_bars,
                        mae_touch_r = EXCLUDED.mae_touch_r, mae_close_r = EXCLUDED.mae_close_r,
                        mae_daily_r = EXCLUDED.mae_daily_r, mfe_r = EXCLUDED.mfe_r,
                        mfe_pre_mae_r = EXCLUDED.mfe_pre_mae_r, t_mae_days = EXCLUDED.t_mae_days,
                        t_exit_days = EXCLUDED.t_exit_days,
                        touched_stop_first = EXCLUDED.touched_stop_first,
                        post_days = EXCLUDED.post_days, post_max_r = EXCLUDED.post_max_r,
                        post_min_r = EXCLUDED.post_min_r, reclaim_day = EXCLUDED.reclaim_day,
                        target_touched = EXCLUDED.target_touched, verdict = EXCLUDED.verdict,
                        live_match = EXCLUDED.live_match, cf = EXCLUDED.cf,
                        exit_day_close_r = EXCLUDED.exit_day_close_r,
                        phantom_stop = EXCLUDED.phantom_stop, graded_at = now()""",
                          {**g, "cf": json.dumps(g["cf"])})
            conn.commit()
            n += 1
        log.info("[swing_mae] graded %d/%d swing fills", n, len(rows))
        return True
    finally:
        conn.close()


READOUT_SQL = """
-- 1. the claim: where the stop sat vs where the trade went (NULL-guarded)
SELECT verdict, count(*) n,
       round(avg(stop_pct),2) stop_pct, round(avg(stop_atr),2) stop_atr,
       round(avg(mae_touch_r),2) mae_touch, round(avg(mae_daily_r),2) mae_daily,
       round(avg(mfe_r),2) mfe, round(avg(mfe_pre_mae_r),2) mfe_pre_mae,
       count(*) FILTER (WHERE touched_stop_first) touched_first,
       round(avg(t_exit_days),1) days
FROM swing_mae_events GROUP BY verdict ORDER BY n DESC;
-- 2. counterfactual stops on the same entries (holes counted, never zero)
SELECT v.name,
       count(*) FILTER (WHERE (cf->v.name->>'outcome') IN ('stop','target','cap')) resolved,
       count(*) FILTER (WHERE cf->v.name IS NULL OR (cf->v.name->>'outcome') = 'hole') holes,
       round(sum(LEAST(GREATEST((cf->v.name->>'r')::numeric, -10), 10))
             FILTER (WHERE (cf->v.name->>'r') IS NOT NULL), 2) sum_r,
       count(*) FILTER (WHERE (cf->v.name->>'outcome') = 'target') wins,
       count(*) FILTER (WHERE (cf->v.name->>'outcome') = 'stop') stops
FROM swing_mae_events e, (VALUES ('x1_0'),('x1_5'),('x2_0'),('atr1')) v(name)
WHERE e.exit_reason IS NOT NULL GROUP BY v.name ORDER BY v.name;
"""
