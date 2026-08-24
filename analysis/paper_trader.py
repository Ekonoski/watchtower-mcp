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
  - Geometry filter: class-aware (2026-08-11) — neckline classes admit at
    their native measured-move 1:1 (their priors were graded there);
    every variable-geometry class needs target room >= 1.5x stop distance.
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
  - Cipher tag (2026-08-12 books onward): every swing spec carries
    `osc_state` — the cipher components at its timeframe's last completed
    bar, stamped at write time, AFTER curation. Measurement only; see the
    doctrine block above cipher_ok().
"""
import datetime as dt
import json
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
# WORST daily one. Priors: pattern_backtest v6 — the first replay AFTER
# the path-1 censorship fix, 357,946 episodes / 2,340 tickers / 2005+
# (read 2026-08-10; v4's numbers were graded on censored survivors and
# are struck). avg realized R · win-1R% · n, bullish, breakout-close
# entries:
#   asc_triangle   weekly +0.28 67% (n=1,896)   daily +0.08 61% (n=6,631)
#   bull_flag      weekly +0.24 67% (n=2,561)   daily +0.09 60% (n=11,496)
#   double_bottom  weekly +0.21 67% (n=2,998)   daily +0.04 63% (n=13,208)
#   higher_low     weekly +0.20 64% (n=9,254)   daily +0.01 61% (n=37,034)
#   inverse_hs     weekly +0.19 63% (n=2,532)   daily +0.07 57% (n=8,798)
#   ema_bounce     weekly +0.84 63% (n=8,188)   daily -0.16 58% (n=46,979) EXCLUDED
#   cup_handle     weekly +0.24 63% (n=2,727)   daily +0.01 57% (n=11,795) EXCLUDED
#   range_breakout weekly +0.19 61% (n=3,423)   daily +0.08 57% (n=22,492) EXCLUDED
#   falling_wedge  weekly +0.12 59% (n=1,750)   daily +0.09 61% (n=7,059)  EXCLUDED
# Every weekly class beats its daily twin — the weekly-beats-daily
# curation rank is now an 8,000-to-47,000-sample fact, not a preference.
# *The daily neckline classes (higher_low, double_bottom) rode in as the
# entry-location experiment (Eric, 2026-08-08) when v4 graded them
# negative; v6 re-grades them marginally POSITIVE, so the experiment
# continues with a friendlier prior — same review gate: judged on their
# own resolved live trades at ~30, retest entries vs these breakout-close
# grades. Survivorship caveat stands: pre-2021 delistings never entered
# daily_prices, so all bullish priors read optimistic. spy_above regime
# tags cover only ~⅓ of v6 episodes (nulls elsewhere) — regime splits are
# directional until that backfills.
SWING_CLASSES = (
    ("higher_low", "weekly"), ("higher_low", "daily"),
    ("double_bottom", "weekly"), ("double_bottom", "daily"),
    ("inverse_hs", "weekly"), ("inverse_hs", "daily"),
    ("asc_triangle", "weekly"), ("asc_triangle", "daily"),
    ("bull_flag", "weekly"), ("bull_flag", "daily"),
    ("ema_bounce", "weekly"),
    # Added 2026-08-10 on the v6 read (Eric): weekly-only — each positive
    # at scale (see table above). Their daily twins stay out: +0.01 to
    # +0.09 edges are too thin to spend capped book slots on, and the
    # daily-neckline slots are already spoken for by the declared
    # experiment.
    ("cup_handle", "weekly"), ("range_breakout", "weekly"),
    ("falling_wedge", "weekly"),
    # The goat line (Eric, 2026-08-10, same evening as the study): a
    # 40-week-qualified uptrend's touch of its 200-WEEK SMA. Prior is the
    # goat study's OWN grade, not pattern_backtest (the replay's 420-day
    # windows can't reach 240 weekly bars yet): 82% to +5% / 58% to +10%,
    # n=2,653, ~126 events/yr universe-wide, survivorship-flattered and
    # blind to 2008 — a declared provisional prior, reviewed like the
    # neckline experiment on its own resolved live trades.
    ("wma_touch", "weekly"),
)
SWING_PATTERNS = tuple(dict.fromkeys(p for p, _tf in SWING_CLASSES))
# The swing book is a CURATED control, not the whole scanner. Weekly/daily
# only (the backtested retest claims: weekly higher_low 63%, daily 50% — the
# 4h was never the retest thesis), one spec per ticker, top-N by score. On
# 2026-08-07 the uncurated query armed 151 blind limits on an NFP morning.
SWING_TIMEFRAMES = ("weekly", "daily")
SWING_MAX = 15
# A flat score bar is a class gate in disguise (2026-08-16, same family as
# the flat 1.5:1 geometry): detector quality scales differ, so the old
# flat `score >= 70` was trivial for higher_low and mathematically out of
# reach for a wedge (falling_wedge quality caps near 20 -> ~78 ceiling
# only at breakout+volume perfection; its best live row scored 61 while
# higher_low printed 78s). Floor picks (one guaranteed slot per class)
# admit at SWING_SCORE_FLOOR; open-competition slots keep the old bar.
SWING_SCORE_OPEN = 70.0
SWING_SCORE_FLOOR = 55.0


# Neckline classes target the measured move: target−trigger EQUALS
# trigger−invalid by construction, so the flat 1.5:1 gate could never admit
# them (caught 2026-08-11 when Eric asked why the book was all higher-lows:
# 91 armable iHS/double-bottom candidates that morning, scores to 83.0,
# ZERO eligible — the "daily neckline experiment" had never actually been
# running). Their v6 priors were graded AT native 1:1 geometry, so the
# gate was double-punishing a trade shape the record already validated.
NECKLINE_CLASSES = {"inverse_hs", "double_bottom"}


def native_geometry_ratio(pattern: str) -> float:
    """Pure. The R:R a class is admitted at — and therefore the R:R every
    later geometry re-check must demand. ONE source: on 2026-08-12 the
    reclaim re-check still carried its own flat 1.5 while admission had
    gone class-aware, and ATRC's 1.5-cent reclaim premium was refused at
    "1.00:1 vs 1.5" on a measured-move class the writer admits at 1:1 —
    a gate a neckline class could never pass, entry-side this time."""
    return 0.95 if pattern in NECKLINE_CLASSES else 1.5


def swing_geometry_ok(pattern: str, trigger: float, target: float,
                      invalid: float) -> bool:
    """Class-aware R:R gate, pure so it pins in a test. Neckline classes
    admit at their native measured-move geometry (>=0.95 tolerates detector
    rounding); every variable-geometry class keeps the 1.5:1 bar."""
    risk = trigger - invalid
    if risk <= 0:
        return False
    ratio = (target - trigger) / risk
    return ratio >= native_geometry_ratio(pattern)


def swing_spec_pattern(setup: str) -> str:
    """Pure. 'retest_double_bottom_weekly' -> 'double_bottom'. The spec row
    carries no pattern column; the setup name is the record, and patterns
    contain underscores, so strip the prefix and the timeframe suffix."""
    s = setup[len("retest_"):] if setup.startswith("retest_") else setup
    return s.rsplit("_", 1)[0]


# ── Cipher tag: measurement only, NEVER a gate (Eric, 2026-08-11) ────────────
# The cipher-at-episodes study (320,144 v6 episodes, run 2026-08-11) split
# the weekly RSI-45-60 cohort 7x: +0.69R with mf-slope-up + MACD-hist-positive
# + wt2 below overbought, vs +0.10R without (n=9,264 / 4,175). That prior was
# graded on breakout-close entries; this desk buys the retest — so the tag
# rides as a shadow label on every swing spec until ~30 resolved weekly
# trades grade the live split, exactly like the confirmation shadow. It must
# not touch arming, curation, fills, or exits during the measurement window —
# a tiebreaker is a gate in disguise and would contaminate the experiment.
# The COMPONENTS carry the signal; the blended 0-100 confluence score
# sign-flips across timeframes (daily 40-58 bucket -0.64R, weekly +0.97R)
# and is recorded for the archive but must never gate anything.
CIPHER_WT_OVERBOUGHT = 53.0   # evaluate_signals' own overbought band edge


def cipher_ok(mf_slope_pos, macd_hist_pos, wt2) -> "bool | None":
    """Pure. The studied weekly-selector condition: money-flow slope up,
    MACD histogram positive, wavetrend not overbought. Returns None when
    any component is missing — a data hole is not a verdict, and the
    ledger renders it as one (never as False)."""
    if mf_slope_pos is None or macd_hist_pos is None or wt2 is None:
        return None
    return bool(mf_slope_pos) and bool(macd_hist_pos) \
        and float(wt2) < CIPHER_WT_OVERBOUGHT


def swing_osc_state(bars, timeframe: str) -> dict:
    """Cipher components at the last COMPLETED bar of the spec's timeframe,
    computed by the SAME code path the episode study graded (state_at over
    compute_oscillator — not a lookalike). bars: daily_prices rows
    (trade_date, open, high, low, close, volume), oldest first, as recorded
    — reconstruction is not tape, so the tag only ever reads stored bars.
    Weekly specs resample with drop_partial=True: at a 7:40 write the
    current week is incomplete and must not leak into the tag.

    ALWAYS returns a renderable dict: the tag, or
    {"cipher_ok": None, "unavailable": reason} — a failed lookup is not a
    neutral reading, and a spec without a tag must say so."""
    try:
        from analysis.cipher_episode_study import state_at, _frame
        from analysis.oscillator import compute_oscillator, resample_weekly
        if len(bars) < 80:
            return {"cipher_ok": None,
                    "unavailable": f"only {len(bars)} daily bars on record"}
        df = _frame(bars)
        if timeframe == "weekly":
            df = resample_weekly(df, drop_partial=True)
            if len(df) < 80:
                return {"cipher_ok": None,
                        "unavailable": f"only {len(df)} completed weeks on record"}
        ind = compute_oscillator(df)
        st = state_at(ind, len(ind) - 1)
        return {
            "asof": ind.index[-1].date().isoformat(),
            "timeframe": timeframe,
            "rsi": None if st["rsi"] is None else round(st["rsi"], 2),
            "wt2": None if st["wt2"] is None else round(st["wt2"], 2),
            "mf": None if st["mf"] is None else round(st["mf"], 2),
            "mf_slope_pos": st["mf_slope_pos"],
            "macd_hist_pos": st["macd_hist_pos"],
            "confluence": st["confluence"],
            "cipher_ok": cipher_ok(st["mf_slope_pos"], st["macd_hist_pos"],
                                   st["wt2"]),
        }
    except Exception as e:
        # Keep the whole reason (>=300 chars rule) — a tag that failed
        # quietly would read as "cipher neutral" downstream.
        return {"cipher_ok": None, "unavailable": str(e)[:300]}


def cipher_tag_label(tag: dict) -> str:
    """Pure. One word per spec for the admissions-style morning log line."""
    v = (tag or {}).get("cipher_ok")
    return "unavailable" if v is None else ("cipher_ok" if v else "cipher_not")


def bearish_conflicts(rows):
    """Pure. rows: (ticker, timeframe, pattern, status, trigger) for LIVE
    bearish structures on tickers the writer is about to arm long. Returns
    {ticker: one warning string}.

    2026-08-13, the CIFR case: the writer queries direction='bullish' only,
    so it armed an 83-score daily inverse_hs blind to a weekly hs_top
    (forming) and a daily lower_high (at RETEST) the same scanner held on
    the same ticker. Doctrine already says breakdown detections serve as
    WARNINGS on held longs — this closes the arming-time blind spot: the
    warning stamps into the spec's rationale (so the ledger carries it) and
    the morning log WARNs. It never gates — shorts are retired, warnings
    are warnings, and a tiebreaker would be a gate in disguise."""
    out = {}
    for tk, tf, pat, st, trig in rows:
        out.setdefault(tk, []).append(
            f"{pat} {tf} {st} (trig {float(trig):g})")
    return {tk: " + ".join(v) for tk, v in out.items()}


def swing_class_ok(pattern: str, timeframe: str) -> bool:
    """The class gate, pure so it pins in a test. The SQL query filters by
    pattern AND timeframe independently; this is the joint filter that
    keeps a pattern's excluded timeframe (ema_bounce daily) out of the
    book even though both its pattern and its timeframe are individually
    tradable."""
    return (pattern, timeframe) in SWING_CLASSES


def fresh_swing_rows(rows, today):
    """Pure. rows: pattern_scan candidate tuples whose LAST element is the
    row's scan date in ET. The freshness gate (2026-08-10): the 6:45 scan
    died in a database brownout and the 7:40 writer armed the whole swing
    book from Friday's rows — TNDM's trigger sat 23% below the market it
    woke up to. A row is armable only if it was scanned TODAY; a dead scan
    morning shrinks the book, loudly — it never pads it with leftovers.
    Returns (fresh_rows_without_date, stale_count, stale_latest)."""
    fresh = [r[:-1] for r in rows if r[-1] == today]
    stale = [r[-1] for r in rows if r[-1] != today]
    return fresh, len(stale), max(stale) if stale else None


def curate_swing(rows, cap=SWING_MAX):
    """Pure. rows: (ticker, timeframe, pattern, direction, trigger, target,
    invalid, score) already geometry-filtered. One spec per ticker (weekly
    beats daily, then higher score); then CLASS-FLOOR curation
    (2026-08-16, Eric: "we need to get these active so we have real
    data"): a flat top-N by score let the two highest-volume classes
    monopolize every slot for the book's whole first week — ~260
    higher_low/neckline candidates against cup_handle's best 67.9 meant
    cup & handle, falling wedge, ema_bounce et al. NEVER armed a spec,
    so their live-vs-prior experiments simply weren't running. Every
    class present in `rows` now gets ONE guaranteed slot (its
    best-scoring candidate — floor picks answer to SWING_SCORE_FLOOR,
    enforced upstream in the candidate query); remaining slots run open
    competition at SWING_SCORE_OPEN+, exactly the old rule.
    Returns (kept, dropped_count, floor_picks) where floor_picks maps
    (pattern, timeframe) -> ticker for the slots the floor granted —
    the writer logs it, and the ledger's "armed today" class mix
    renders the change the day it ships."""
    best = {}
    for r in rows:
        tk, tf, score = r[0], r[1], r[7]
        rank = (tf == "weekly", score)
        if tk not in best or rank > (best[tk][1] == "weekly", best[tk][7]):
            best[tk] = r
    pool = sorted(best.values(), key=lambda r: -r[7])
    by_class = {}
    for r in pool:
        key = (r[2], r[1])
        if key not in by_class:          # pool is score-sorted: first wins
            by_class[key] = r
    floor = sorted(by_class.values(), key=lambda r: -r[7])[:cap]
    kept = list(floor)
    used = {r[0] for r in kept}
    for r in pool:
        if len(kept) >= cap:
            break
        if r[0] in used or r[7] < SWING_SCORE_OPEN:
            continue
        kept.append(r)
        used.add(r[0])
    kept.sort(key=lambda r: -r[7])
    floor_picks = {(r[2], r[1]): r[0] for r in floor}
    return kept, len(rows) - len(kept), floor_picks


def _touch(level, px):
    return abs(px - level) / level <= 0.001


def _qlvl(p: float) -> float:
    """Quantize a level for the spec's NAME only (half-point grid). The
    intraday flip drifts by cents between sweeps, and since the one-shot
    rule keys on (ticker, setup), each cent minted a "new" level —
    2026-08-11's ledger carried ~30 flip_hold cancels that were one level
    wobbling (766.75→767.77). Trigger/stop/target keep full precision;
    only the identity is quantized."""
    return round(p * 2) / 2.0


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
                specs.append((trade_date, book, tk, "short", f"wall_fade_{_qlvl(cw):g}",
                              cw, stop, round(tgt, 2), status,
                              f"first-touch fade at {cw:g} CW, {gex:+.1f}bn pinning; "
                              f"entry=15m close back under wall after touch; "
                              f"stop=15m close beyond {stop}; target {tgt:g}"))
        # Flip-hold long (pinning, flip below spot, room to CW).
        if regime == "pinning" and flip and cw and flip < spot < cw:
            stop = round(flip * 0.9985, 2)
            if (cw - flip) >= 1.5 * (flip - stop):
                specs.append((trade_date, book, tk, "long", f"flip_hold_{_qlvl(flip):g}",
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
                specs.append((trade_date, book, tk, "short", f"stack_fade_{_qlvl(stack):g}",
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
            # Cipher-tag column (2026-08-11): idempotent ensure, committed
            # on its own so an early return can't roll the DDL back.
            c.execute("ALTER TABLE paper_specs ADD COLUMN IF NOT EXISTS osc_state jsonb")
            # Sector-rotation tag column (2026-08-22): same doctrine.
            c.execute("ALTER TABLE paper_specs ADD COLUMN IF NOT EXISTS sector_state jsonb")
        conn.commit()
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
            gamma_specs, skips = build_gamma_specs(today, c.fetchall(), status)
            for tk, why_skip in skips:
                log.info("[paper] %s: no gamma spec — %s", tk, why_skip)
            # The cipher was studied on structure breakouts, not gamma
            # mechanics — gamma specs carry no cipher or sector tag,
            # deliberately (index/mega-cap venues aren't sector trades).
            specs = [s + (None, None) for s in gamma_specs]

            # Swing book: breakout-retest limits (blind by design — control group).
            # Each row carries its own scan date so the freshness gate can
            # judge per row — stamp freshness per row, not per page.
            c.execute("""SELECT ticker, timeframe, pattern, direction, trigger_price,
                                target, invalid_level, score,
                                (scanned_at AT TIME ZONE 'America/New_York')::date
                         FROM pattern_scan
                         WHERE pattern = ANY(%s) AND status='breakout' AND score >= %s
                           AND direction='bullish' AND timeframe = ANY(%s)
                           AND dist_to_trigger_pct BETWEEN 0 AND 4""",
                      (list(SWING_PATTERNS), SWING_SCORE_FLOOR,
                       list(SWING_TIMEFRAMES)))
            rows, n_stale, stale_latest = fresh_swing_rows(c.fetchall(), today)
            if n_stale:
                log.warning("[paper] swing: %d candidate row(s) EXCLUDED as "
                            "stale (latest %s, today %s) — the morning scan "
                            "didn't finish; the book shrinks rather than "
                            "arming yesterday's menu", n_stale, stale_latest, today)
            # Tickers with an OPEN swing position never re-arm (2026-08-11:
            # the writer re-armed COR at 320.58 while Monday's COR position
            # was working at that exact entry — an honest fill would have
            # been an undecided doubling).
            c.execute("""SELECT DISTINCT s.ticker FROM paper_specs s
                         JOIN paper_trades t ON t.spec_id = s.id
                         WHERE s.book='swing' AND t.exited_at IS NULL""")
            open_tks = {r[0] for r in c.fetchall()}
            candidates = [(tk, tf, pat, d, float(trig), float(tgt), float(inv), score)
                          for tk, tf, pat, d, trig, tgt, inv, score in rows
                          if tk not in open_tks
                          and swing_class_ok(pat, tf)
                          and swing_geometry_ok(pat, float(trig), float(tgt), float(inv))]
            for tk in sorted(open_tks & {r[0] for r in rows}):
                log.info("[paper] swing: %s skipped — position already open", tk)
            # Class-admission audit — assert admission, not just detection
            # (2026-08-11: iHS/double-bottom sat allowlisted-but-unarmable
            # for days and nothing said so). One line, every morning.
            from collections import Counter
            band = Counter(f"{r[2]}/{r[1]}" for r in rows)
            admit = Counter(f"{c_[2]}/{c_[1]}" for c_ in candidates)
            log.info("[paper] class admissions (in-band -> eligible): %s",
                     ", ".join(f"{k} {band[k]}->{admit.get(k, 0)}"
                               for k in sorted(band)))
            zero_admit = [k for k in band if band[k] >= 5 and admit.get(k, 0) == 0]
            if zero_admit:
                log.warning("[paper] class(es) with candidates but ZERO "
                            "eligible: %s — if this persists, a gate is "
                            "structurally excluding them", ", ".join(zero_admit))
            kept, dropped, floor_picks = curate_swing(candidates)
            if dropped:
                log.info("[paper] swing: %d qualified, curated to %d (dropped %d)",
                         len(candidates), len(kept), dropped)
            # The class-floor grants and the resulting mix, every morning —
            # a monoculture must render as a question, never as "what the
            # scanner found".
            log.info("[paper] swing class floors: %s",
                     ", ".join(f"{p}/{tf}:{tk}"
                               for (p, tf), tk in sorted(floor_picks.items()))
                     or "none")
            mix = Counter(f"{k[2]}/{k[1]}" for k in kept)
            log.info("[paper] swing armed class mix: %s",
                     ", ".join(f"{k} {v}" for k, v in sorted(mix.items())) or "none")
            # Bearish-structure warning (2026-08-13, CIFR): the bullish-only
            # candidate query can't see the scanner's OTHER opinion of the
            # same ticker. Stamp it, never gate on it.
            warns = {}
            if kept:
                c.execute("""SELECT ticker, timeframe, pattern, status,
                                    trigger_price
                             FROM pattern_scan
                             WHERE ticker = ANY(%s) AND direction='bearish'
                               AND status IN ('forming','retest','breakout')""",
                          ([k[0] for k in kept],))
                warns = bearish_conflicts(c.fetchall())
                for tk, w in sorted(warns.items()):
                    log.warning("[paper] swing: %s armed LONG against live "
                                "bearish structure(s): %s", tk, w)
            # Cipher tag per kept spec (measurement only — the tag is
            # computed AFTER curation so it cannot influence which specs
            # arm, even accidentally). ~15 history reads at 7:40; the same
            # per-ticker cost the episode study paid.
            # Sector-rotation tag (2026-08-22): same contract as the
            # cipher tag — computed AFTER curation so arming stays blind,
            # holes carry reasons, freshness stamped per row. The cache
            # self-heals here (recorded closes only) so no cron is owed.
            try:
                from analysis.sector_rs import ensure_recent, sector_state_for
                ensure_recent(conn)
            except Exception as e:
                log.warning("[paper] sector RS cache upkeep failed — tags "
                            "will render holes: %s", e)
                sector_state_for = None
            tag_mix = {}
            sec_mix = {}
            for tk, tf, pat, _dir, trig, tgt, inv, score in kept:
                c.execute("""SELECT trade_date, COALESCE(open, close),
                                    COALESCE(high, close), COALESCE(low, close),
                                    close, COALESCE(volume, 0)
                             FROM daily_prices WHERE ticker=%s AND close IS NOT NULL
                             ORDER BY trade_date""", (tk,))
                tag = swing_osc_state(c.fetchall(), tf)
                tag_mix[tk] = cipher_tag_label(tag)
                rationale = (f"{pat} {tf} breakout (score {score}); blind limit at the "
                             f"trigger per retest doctrine; stop=pattern invalid {inv:g}")
                if tk in warns:
                    rationale += f" | ⚠ bearish structure live: {warns[tk]}"
                if sector_state_for is not None:
                    try:
                        stag = sector_state_for(conn, tk)
                    except Exception as e:
                        stag = {"sector": None,
                                "reason": f"tag_error: {str(e)[:300]}"}
                else:
                    stag = {"sector": None, "reason": "rs_cache_unavailable"}
                sec_mix[tk] = (f"{stag['sector']}#{stag['rank_1m']}"
                               if "rank_1m" in stag else "unavailable")
                specs.append((today, "swing", tk, "long", f"retest_{pat}_{tf}",
                              trig, inv, tgt, "armed", rationale,
                              json.dumps(tag), json.dumps(stag)))
            if tag_mix:
                from collections import Counter as _Counter
                mix = _Counter(tag_mix.values())
                log.info("[paper] swing cipher tags (measurement only, never a "
                         "gate): %s [%s]",
                         ", ".join(f"{k} {v}" for k, v in sorted(mix.items())),
                         ", ".join(f"{tk}:{lbl}" for tk, lbl in sorted(tag_mix.items())))
            if sec_mix:
                # Sector mix, every morning — a book concentrated in an
                # outflow sector must render as a question, same family
                # as the armed-class-mix line.
                log.info("[paper] swing sector tags (measurement only, "
                         "never a gate): %s",
                         ", ".join(f"{tk}:{s}" for tk, s in sorted(sec_mix.items())))

        with conn.cursor() as c:
            c.executemany("""INSERT INTO paper_specs
                (trade_date, book, ticker, direction, setup, entry_trigger, stop,
                 target, status, rationale, osc_state, sector_state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", specs)
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
                vol = b.get("volume")
                out.append((te, float(b["open"]), float(b["close"]),
                            float(b["high"]), float(b["low"]),
                            float(vol) if vol is not None else None))
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
    for _, bop, bc2, bhi, blo, *_xv in live_bars:
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


def _entry_geometry_ok(direction: str, entry: float, stop: float,
                       tgt: float, ratio: float = 1.5):
    """Geometry must survive the entry (Eric, 2026-08-08: same standard,
    no special cases — there will be plenty of entries). The spec-writer
    demands the class's NATIVE ratio at the TRIGGER; a violent reclaim
    premium can quietly collapse that (TNDM 2026-08-07: 2.1:1 at the 18.16
    trigger became 0.79:1 at the real 19.62 entry). Re-checked at the
    actual fill price on any entry that isn't the trigger, against the
    SAME ratio the spec qualified on — callers pass
    native_geometry_ratio(pattern); demanding a flat 1.5 here refused
    ATRC's 1.5-cent reclaim premium on a 1:1 measured-move class
    (2026-08-12). Collapsed geometry cancels instead of filling, and the
    refusal stays gradeable from recorded bars. Returns (ok, actual_ratio)."""
    sign = 1 if direction == "long" else -1
    reward, risk = sign * (tgt - entry), sign * (entry - stop)
    if risk <= 0:
        return (False, 0.0)
    r = reward / risk
    return (r >= ratio, r)


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
    for ts, _bop, bc2, bhi, blo, *_xv in live_bars:
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
    out = []
    for b in bars:
        ts, op, cl, hi, lo = b[0], b[1], b[2], b[3], b[4]
        vol = b[5] if len(b) > 5 else None
        out.append((tk, ts, op, hi, lo, cl, vol, trade_date))
    return out


def _persist_spec_bars(conn, tk, trade_date, bars):
    """Reconstruction is not tape (TNDM, 2026-08-08): audits must replay
    from bars the loop actually decided on, not refetched history. Runs
    every pass, idempotent — a mid-day crash keeps everything seen so far."""
    rows = _spec_bar_rows(tk, trade_date, bars)
    if not rows:
        return
    with conn.cursor() as c:
        c.executemany("""INSERT INTO paper_spec_bars
                         (ticker, ts, open, high, low, close, volume,
                          trade_date)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
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


def persist_closing_bars():
    """One persistence-only pass after the close (scheduled ~16:07 ET).

    The loop's last pass runs by 15:58 — before the 15:45–16:00 bar
    completes — so the day's FINAL bar never reached paper_spec_bars
    (2026-08-10: COR ran three points into the close and the record ended
    at the 15:45 bar; Eric's chart said 325, ours said 321.85). Bars are
    persisted whenever seen; the closing bar is the one most worth seeing.
    No fills, no exits, no state changes — persistence only."""
    now = dt.datetime.now(ET)
    if now.weekday() >= 5:
        return
    today = now.date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT s.ticker
                         FROM paper_specs s LEFT JOIN paper_trades t ON t.spec_id=s.id
                         WHERE s.trade_date=%s
                            OR (s.status='triggered' AND t.exited_at IS NULL)""",
                      (today,))
            tks = [r[0] for r in c.fetchall()]
        n_ok = 0
        for tk in tks:
            bars = _last_closed_15m(tk)
            if not bars:
                continue
            try:
                _persist_spec_bars(conn, tk, today, bars)
                n_ok += 1
            except Exception:
                conn.rollback()
                log.exception("[paper] closing-bar persist failed for %s", tk)
        log.info("[paper] closing-bar pass: %d/%d tickers persisted", n_ok, len(tks))
    finally:
        conn.close()


# The final RTH bar starts at 15:45 ET; anything earlier is not the close.
SETTLE_FINAL_BAR_START = dt.time(15, 45)


def swing_settle_decision(direction: str, stop: float, target: float, final_bar):
    """Pure. Exit decision for a swing position on the TRUE daily close —
    the completed 15:45–16:00 bar. Same rules and precedence as the live
    loop's eod branch: stop accepts on the bar's CLOSE beyond the stop
    (wick rule), target on a touch. Returns (exit_px, reason) or (None,
    None).

    Exists because the loop's window ends at 15:58, so its 'daily close'
    was really the 15:30–15:45 bar — and on 2026-08-14 AGMB closed that
    bar at 13.16 (0.9 cents ABOVE its 13.1507 stop) then printed the true
    close at 13.03, and the stop never fired. A stop that breaks at 3:52
    must count exactly like one that breaks at 3:44 — one rule for
    winners and losers alike."""
    ts, op_, close, hi, lo = final_bar
    sign = 1 if direction == "long" else -1
    if sign * (close - stop) < 0:
        return close, "stop"
    if (direction == "long" and hi >= target) or \
            (direction == "short" and lo <= target):
        return target, "target"
    return None, None


def run_swing_close_settle():
    """Post-close settling pass (~16:20 ET, after the 16:07 closing-bar
    persist): decide open swing positions on the day's RECORDED final RTH
    bar. Reads paper_spec_bars only — never refetches (reconstruction is
    not tape); a missing final bar is a logged hole, not a decision."""
    now = dt.datetime.now(ET)
    if now.weekday() >= 5 or now.time() < dt.time(16, 10):
        return
    today = now.date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT s.ticker, s.direction, s.stop, s.target,
                                t.id, t.entry_px, t.entered_at
                         FROM paper_specs s JOIN paper_trades t ON t.spec_id=s.id
                         WHERE s.book='swing' AND t.exited_at IS NULL""")
            rows = c.fetchall()
        for tk, direction, stop, tgt, tid, entry_px, entered_at in rows:
            stop, tgt, entry_px = float(stop), float(tgt), float(entry_px)
            with conn.cursor() as c:
                c.execute("""SELECT ts, open, close, high, low FROM paper_spec_bars
                             WHERE ticker=%s AND trade_date=%s
                             ORDER BY ts DESC LIMIT 1""", (tk, today))
                row = c.fetchone()
            if row is None or row[0].astimezone(ET).time() < SETTLE_FINAL_BAR_START:
                log.warning("[paper] settle: %s final bar not on record — "
                            "hole, no decision", tk)
                continue
            final = (row[0], float(row[1]), float(row[2]),
                     float(row[3]), float(row[4]))
            # Entry-bar lookahead guard, same as the loop: the entry bar's
            # range is pre-entry price action.
            if entered_at is not None and \
                    final[0] + dt.timedelta(minutes=15) <= entered_at:
                continue
            exit_px, reason = swing_settle_decision(direction, stop, tgt, final)
            if exit_px is None:
                continue
            sign = 1 if direction == "long" else -1
            r_dist = abs(entry_px - stop) or 0.01
            r_mult = round(sign * (exit_px - entry_px) / r_dist, 2)
            with conn.cursor() as c:
                c.execute("""UPDATE paper_trades SET exited_at=now(), exit_px=%s,
                             exit_reason=%s, r_multiple=%s WHERE id=%s""",
                          (exit_px, reason, r_mult, tid))
            conn.commit()
            log.info("[paper] SETTLE-EXIT swing %s %s @ %.2f (%s, %+.2fR) — "
                     "true daily close", tk, direction, exit_px, reason, r_mult)
    finally:
        conn.close()


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
        try:
            # Binary-day shadow: bars + 10:30 decision + grade for today's
            # skipped_binary specs. Runs BEFORE the armed/triggered query —
            # a pure binary day has no live rows, and the shadow is the
            # only thing keeping that day's record from being empty.
            run_binary_shadow(conn, today, now)
        except Exception:
            conn.rollback()
            log.exception("[paper] binary shadow failed — live loop unaffected")
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
            # 2026-08-24: the SIXTH element (volume) landed Friday night;
            # this was the one unpack site the star-tolerance sweep
            # missed, and it took the whole loop down at Monday's open.
            ts, op_, close, hi, lo, *_v = bars[-1]
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
                                  for _, _, c2, hi2, lo2, *_xv in live_bars)
                    entered = touched and (sign * (close - trig) > 0)  # 15m close back through
                    entry_fill, kind = close, "close_through"
                if entered and kind == "reclaim":
                    # The reclaim premium repriced the trade — the geometry
                    # the spec QUALIFIED on must survive the actual entry:
                    # the class's native ratio, not a flat 1.5 (ATRC,
                    # 2026-08-12 — see native_geometry_ratio).
                    req = native_geometry_ratio(swing_spec_pattern(setup))
                    ok, ratio = _entry_geometry_ok(direction, entry_fill,
                                                   stop, tgt, req)
                    if not ok:
                        _cancel(conn, sid,
                                f"reclaim_geometry — {ratio:.2f}:1 at entry "
                                f"{entry_fill:g} (target {tgt:g}, stop {stop:g}); "
                                f"spec demanded {req:g}:1")
                        log.info("[paper] REFUSE %s %s reclaim @ %.2f — "
                                 "geometry %.2f:1", book, tk, entry_fill, ratio)
                        continue
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


# ── Binary-day shadow re-arm (Eric, 2026-08-12, the CPI post-mortem) ─────────
# The binary gate skips the WHOLE day; the print resolves by mid-morning.
# On 2026-08-12 every recorded gex snapshot from 9:35 on read pinning and
# none of the four skipped triggers ever printed — the skip cost 0R, but the
# desk only knew from snapshots because the watcher never subscribed to the
# skipped tickers. Whether the full-day skip over-pays is measured, not
# argued — the confirmation-shadow pattern: skipped_binary specs
# shadow-re-arm at 10:30 ET if the recorded 10:30 board still shows their
# level, then grade by the live gamma rules from recorded bars. The shadow
# never places a trade and never touches spec status; promotion (or the
# skip's vindication) waits on ~30 shadow-resolved specs, small-n rule.

SHADOW_REARM_TIME = dt.time(10, 30)
SHADOW_FRESH_MIN = IDAY_FRESH_MIN   # same freshness bar as the intraday armer
# 0.25%: the flip's cent-wobble between sweeps matches (716.65 vs 716.09 —
# the same level _qlvl would misname across its half-point grid); a real
# wall migration (775 -> 780, 0.65%) does not.
SHADOW_LEVEL_TOL = 0.0025


def shadow_rearm_decision(ticker, setup, trigger, live_specs,
                          tol=SHADOW_LEVEL_TOL):
    """Pure. Does the 10:30 board still show this skipped spec's level?

    live_specs: build_gamma_specs output off the recorded 10:30 board — so
    the regime check is implicit (a wall_fade only exists under pinning) and
    every arming gate (magnitude, collapsed magnet, geometry) is re-applied
    by the same code the live books use. A match is same ticker + same setup
    FAMILY + trigger within tol; the family compares because the quantized
    setup NAME calls a 0.08% flip wobble a different level (see _qlvl) and
    the shadow asks about the level, not its name.

    Returns (rearmed, reason) — the reason names itself in both directions,
    so a no-re-arm ledger row is a decision, never a blank."""
    fam = setup.rsplit("_", 1)[0]
    for sp in live_specs:
        if sp[2] != ticker:
            continue
        live_setup, live_trig = sp[4], float(sp[5])
        if live_setup.rsplit("_", 1)[0] == fam \
                and abs(live_trig - trigger) / trigger <= tol:
            return True, (f"level held at 10:30 — board shows {live_setup} "
                          f"trigger {live_trig:g} vs spec {trigger:g}")
    return False, ("level absent from the 10:30 board — wall/flip moved or "
                   "regime changed; the morning's trade no longer existed")


def shadow_outcome(direction, trig, stop, tgt, bars):
    """Pure. Grade a shadow-re-armed gamma spec by the live book's own
    rules, replayed from recorded bars. bars: [(ts, open, close, high, low)]
    completed regular-session 15m bars ending AFTER the 10:30 decision,
    oldest first (the caller applies _rth and the post-decision cut).

    Mirrors run_trigger_loop's gamma path exactly: entry is a touch (bar
    range spans the trigger, or a close within 0.1%) followed by the first
    bar CLOSING back through — at that bar's close, the touch bar itself
    counting when its own close is back through; no entries on bars ending
    at/after 14:30 ET (the live no-new clock). Exits: 15m close beyond the
    stop (at that close) or a target touch (at the target), decided only on
    bars the live loop could decide on (ending by 15:45); an open trade
    flattens eod_flat at the close of the last such bar — the same bar the
    live 15:55 pass reads — but only if the record proves the day reached
    the flat window (a bar starting >= 15:30 exists). R from the ACTUAL
    shadow entry. Declared simplification: each shadow grades alone — the
    live two-stop book halt is not simulated (a shadow book of at most a
    few specs rarely reaches it, and cross-spec state would make the
    replay order-dependent).

    Returns {entered_at, entry_px, exited_at, exit_px, exit_reason,
    r_multiple}. entered_at None = the trigger never filled ("the skip cost
    0R", from tape). exited_at None with an entry = the record ended
    mid-trade — a hole, never graded as a flat close."""
    sign = 1 if direction == "long" else -1
    out = {"entered_at": None, "entry_px": None, "exited_at": None,
           "exit_px": None, "exit_reason": None, "r_multiple": None}
    touched = False
    last_decidable = None
    for ts, _bop, bc, bhi, blo, *_xv in bars:
        bar_end = ts + dt.timedelta(minutes=15)
        if bar_end.time() > dt.time(15, 45) or ts.time() > dt.time(15, 30):
            continue                       # the live loop never decides here
        last_decidable = (ts, bc)
        if out["entered_at"] is None:
            touched = touched or (blo <= trig <= bhi) or _touch(trig, bc)
            if touched and sign * (bc - trig) > 0 \
                    and bar_end.time() < IDAY_LAST_NEW:
                out["entered_at"], out["entry_px"] = bar_end, bc
            continue
        if sign * (bc - stop) < 0:
            out["exited_at"], out["exit_px"] = bar_end, bc
            out["exit_reason"] = "stop"
        elif (direction == "long" and bhi >= tgt) \
                or (direction == "short" and blo <= tgt):
            out["exited_at"], out["exit_px"] = bar_end, tgt
            out["exit_reason"] = "target"
        if out["exited_at"] is not None:
            break
    if out["entered_at"] is not None and out["exited_at"] is None \
            and last_decidable is not None \
            and last_decidable[0].time() >= dt.time(15, 30):
        out["exited_at"] = last_decidable[0] + dt.timedelta(minutes=15)
        out["exit_px"], out["exit_reason"] = last_decidable[1], "eod_flat"
    if out["exited_at"] is not None:
        r_dist = abs(out["entry_px"] - stop) or 0.01
        out["r_multiple"] = round(
            sign * (out["exit_px"] - out["entry_px"]) / r_dist, 2)
    return out


def _ensure_shadow_table(conn):
    """Idempotent, committed on its own (the osc_state pattern): the loop
    must not depend on the migration having reached the live database."""
    with conn.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS paper_shadow_rearm (
            spec_id     bigint PRIMARY KEY REFERENCES paper_specs(id),
            decided_at  timestamptz NOT NULL,
            rearmed     boolean,
            reason      text NOT NULL,
            entered_at  timestamptz,
            entry_px    numeric,
            exited_at   timestamptz,
            exit_px     numeric,
            exit_reason text,
            r_multiple  numeric,
            updated_at  timestamptz NOT NULL DEFAULT now())""")
        c.execute("ALTER TABLE paper_shadow_rearm ENABLE ROW LEVEL SECURITY")
    conn.commit()


def run_binary_shadow(conn, today, now):
    """Bars, decision, grade — for today's skipped_binary specs. Called from
    every trigger-loop pass; every step idempotent; reads paper_specs but
    never writes it (the shadow is a measurement riding beside the skip)."""
    with conn.cursor() as c:
        c.execute("""SELECT id, ticker, direction, setup, entry_trigger,
                            stop, target
                     FROM paper_specs
                     WHERE trade_date=%s AND status='skipped_binary'""",
                  (today,))
        specs = c.fetchall()
    if not specs:
        return
    _ensure_shadow_table(conn)
    # Watch the tape. Skipped specs never reach the main loop, so without
    # this a binary day records NOTHING for its own tickers and the
    # counterfactual is unanswerable from the desk's own record.
    bars_by_tk = {}
    for tk in sorted({s[1] for s in specs}):
        bars = _last_closed_15m(tk)
        bars_by_tk[tk] = bars
        if not bars:
            continue
        try:
            _persist_spec_bars(conn, tk, today, bars)
        except Exception:
            conn.rollback()
            log.exception("[paper] shadow bar persist failed for %s — "
                          "loop continues, the record has a hole", tk)
    if now.time() < SHADOW_REARM_TIME:
        return
    decision_ts = dt.datetime.combine(today, SHADOW_REARM_TIME, tzinfo=ET)
    with conn.cursor() as c:
        c.execute("SELECT spec_id, rearmed FROM paper_shadow_rearm "
                  "WHERE spec_id = ANY(%s)", ([s[0] for s in specs],))
        decided = dict(c.fetchall())
    undecided = [s for s in specs if s[0] not in decided]
    if undecided:
        # The 10:30 decision, stamped once, from the RECORDED board — the
        # freshest gex_intraday row at or before 10:30, same staleness bar
        # as the live armer — so the decision replays offline byte-for-byte.
        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT ON (ticker) ticker, spot, call_wall,
                                put_wall, gamma_flip, net_gex, regime
                         FROM gex_intraday
                         WHERE ticker = ANY(%s) AND ts <= %s
                           AND ts > %s - %s * interval '1 minute'
                         ORDER BY ticker, ts DESC""",
                      (VENUE, decision_ts, decision_ts, SHADOW_FRESH_MIN))
            board = c.fetchall()
        board_tks = {b[0] for b in board}
        live_specs, _ = build_gamma_specs(today, board, "shadow") \
            if board else ([], [])
        for sid, tk, direction, setup, trig, _stop, _tgt in undecided:
            if tk not in board_tks:
                rearmed, reason = None, (
                    f"10:30 board unavailable — no gex_intraday row for "
                    f"{tk} within {SHADOW_FRESH_MIN} min of the decision")
            else:
                rearmed, reason = shadow_rearm_decision(
                    tk, setup, float(trig), live_specs)
            with conn.cursor() as c:
                c.execute("""INSERT INTO paper_shadow_rearm
                             (spec_id, decided_at, rearmed, reason)
                             VALUES (%s,%s,%s,%s)
                             ON CONFLICT (spec_id) DO NOTHING""",
                          (sid, decision_ts, rearmed, reason))
            conn.commit()
            decided[sid] = rearmed
            log.info("[paper] shadow decision %s %s: rearmed=%s — %s",
                     tk, setup, rearmed, reason)
    # Grade re-armed shadows: pure recompute from recorded bars, upserted
    # every pass — no incremental state to corrupt across restarts.
    for sid, tk, direction, _setup, trig, stop, tgt in specs:
        if decided.get(sid) is not True:
            continue
        live = [b for b in _rth(bars_by_tk.get(tk, []))
                if b[0] + dt.timedelta(minutes=15) > decision_ts]
        out = shadow_outcome(direction, float(trig), float(stop),
                             float(tgt), live)
        with conn.cursor() as c:
            c.execute("""UPDATE paper_shadow_rearm
                         SET entered_at=%s, entry_px=%s, exited_at=%s,
                             exit_px=%s, exit_reason=%s, r_multiple=%s,
                             updated_at=now()
                         WHERE spec_id=%s""",
                      (out["entered_at"], out["entry_px"], out["exited_at"],
                       out["exit_px"], out["exit_reason"],
                       out["r_multiple"], sid))
        conn.commit()
