"""
The DAY-TYPE study (2026-09-08, Eric, after a −1.34R chop day: "we need
to identify chop vs a red or green day… we know when a day will most
likely be green 80% of the time, but we don't know when it will be red,
or definitively choppy").

What the desk already knows: open > PDH closes above the prior close
79.5% (SPY, 5,443 days); open < PDL closes below it 76% — the BIAS is
known both ways, the red side just has no graded entry. What has never
been graded is CHOP as a day type: a day whose range never expands and
whose close sits in its middle — the day where every directional option
bleeds theta. This study labels every index day at the close and records
what was readable at 9:45, 10:00 and 10:30 with NO lookahead, so the
readout can say "given this morning, P(chop) / P(trend) = …, n = …".

SPEC (frozen before any number):
  universe   SPY (2005→) and QQQ (2011→) from index_intraday_bars (15m
             RTH) + daily_prices; a day needs the first four RTH bars and
             20 prior days for ATR.
  label      range_ratio = (H−L)/ATR20 (prior 20 completed days' true
             range); close_pos = (C−L)/(H−L).
             trend_green: close_pos ≥ 0.75, range_ratio ≥ 0.9, C > O
             trend_red:   close_pos ≤ 0.25, range_ratio ≥ 0.9, C < O
             chop:        range_ratio < 0.75 OR 0.35 ≤ close_pos ≤ 0.65
             mixed:       everything else
             (the continuous values are stored so the cut can be re-drawn)
  reads      at each checkpoint (bars completed by 9:45 / 10:00 / 10:30):
             open_state (above_pdh / inside / below_pdl), gap_bucket,
             prev_close_pos, prev_range_ratio bucket (yesterday's range
             vs ATR — contraction persists?), prev_day_dir,
             orb_ratio = (high−low so far)/ATR20 bucketed, pos_in_orb,
             orb_break (10:30 only: a bar after the first 30 min CLOSED
             above the 30-min high / below its low / neither — wick
             rule), vix_backwardated + vix_bucket (prior close),
             gamma_regime + flip_prox (2026-07-15 on, exploratory),
             weekday.
  readout    per checkpoint × leg value: P(trend either side), P(chop),
             P(green) among trends, n; both eras (pre/post 2016) and SPY
             vs QQQ replication; cells under n=40 render small-n. A
             surviving state becomes a STAND-ASIDE or an ENABLE read on
             the 📐 line — measurement first, never a gate by feel.
Writes ONLY daytype_days / daytype_progress. Marker daytype_v1.
"""
import datetime as dt
import json
import logging

log = logging.getLogger("watchtower.daytype")

COMPLETE_MARKER = "daytype_v2"   # v1 divided by an ATR holding today's own bar; re-seeded 2026-09-08
TICKERS = ("SPY", "QQQ")
GAMMA_FROM = dt.date(2026, 7, 15)
CHECKPOINTS = ((dt.time(9, 45), "f945"), (dt.time(10, 0), "f1000"), (dt.time(10, 30), "f1030"))


# ── pure ─────────────────────────────────────────────────────────────

def true_range(h, l, prev_close):
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def label_day(o, h, l, c, atr20):
    """Pure. (label, range_ratio, close_pos). A zero range or missing ATR
    is a hole (None label)."""
    if atr20 is None or atr20 <= 0 or h <= l:
        return None, None, None
    rr = (h - l) / atr20
    cp = (c - l) / (h - l)
    if cp >= 0.75 and rr >= 0.9 and c > o:
        lab = "trend_green"
    elif cp <= 0.25 and rr >= 0.9 and c < o:
        lab = "trend_red"
    elif rr < 0.75 or 0.35 <= cp <= 0.65:
        lab = "chop"
    else:
        lab = "mixed"
    return lab, round(rr, 3), round(cp, 3)


def _bucket_ratio(x):
    if x is None:
        return None
    return "a<0.25" if x < 0.25 else "b0.25-0.5" if x < 0.5 else "c0.5-0.75" if x < 0.75 else "d0.75-1" if x < 1.0 else "e1+"


def _gap_bucket(pct):
    a = abs(pct)
    b = "a<0.3" if a < 0.3 else "b0.3-1" if a < 1 else "c1-2" if a < 2 else "d2+"
    return f"{'+' if pct >= 0 else '-'}{b}"


def features(bars, prev, atr20, vix_row=None, gamma=None, weekday=None):
    """Pure. bars: the RTH 15m bars completed by the checkpoint, each
    (ts, open, high, low, close) in order; prev: yesterday's daily
    {open,high,low,close, range_ratio}. Reads only what the checkpoint
    could see. Missing inputs render None (holes)."""
    out = {}
    if not bars or not prev or not atr20:
        return out
    o = bars[0][1]
    out["open_state"] = "above_pdh" if o > prev["high"] else "below_pdl" if o < prev["low"] else "inside"
    out["gap_bucket"] = _gap_bucket((o / prev["close"] - 1) * 100)
    rng = prev["high"] - prev["low"]
    pos = (prev["close"] - prev["low"]) / rng if rng > 0 else None
    out["prev_close_pos"] = None if pos is None else ("top20" if pos >= 0.8 else "bottom20" if pos <= 0.2 else "mid")
    out["prev_range_ratio"] = _bucket_ratio(prev.get("range_ratio"))
    out["prev_day_dir"] = prev.get("dir")
    hi = max(b[2] for b in bars)
    lo = min(b[3] for b in bars)
    last = bars[-1][4]
    out["orb_ratio"] = _bucket_ratio((hi - lo) / atr20)
    out["pos_in_orb"] = None if hi <= lo else ("top" if (last - lo) / (hi - lo) >= 0.75 else "bottom" if (last - lo) / (hi - lo) <= 0.25 else "mid")
    out["last_vs_open"] = "up" if last > o else "down" if last < o else "flat"
    if len(bars) >= 4:
        h30 = max(b[2] for b in bars[:2])
        l30 = min(b[3] for b in bars[:2])
        later = bars[2:]
        up = any(b[4] > h30 for b in later)
        dn = any(b[4] < l30 for b in later)
        out["orb_break"] = "both" if up and dn else "up" if up else "down" if dn else "none"
    if vix_row:
        v, v3 = vix_row.get("vix"), vix_row.get("vix3m")
        out["vix_backwardated"] = None if v is None or v3 is None else v > v3
        out["vix_bucket"] = None if v is None else ("a<15" if v < 15 else "b15-20" if v < 20 else "c20-30" if v < 30 else "d30+")
    else:
        out["vix_backwardated"] = None
        out["vix_bucket"] = None
    if gamma:
        out["gamma_regime"] = gamma.get("regime")
        flip = gamma.get("flip")
        out["flip_prox"] = None if not flip else ("a<0.3" if abs(o - flip) / o * 100 < 0.3 else "b0.3-0.6" if abs(o - flip) / o * 100 < 0.6 else "c0.6+")
    else:
        out["gamma_regime"] = None
        out["flip_prox"] = None
    out["weekday"] = weekday
    return out


def daily_facts(daily):
    """Pure. daily: [(date, open, high, low, close)] ascending. Returns
    (atr, prevd), both keyed by date d and built ONLY from bars before d:
    atr[d] is the 20-bar ATR through the PRIOR close (the first cut of
    the seeder divided today's opening range by an ATR that already held
    today's full-day true range — a 1/20 lookahead that flattered the
    tight-first-bar → chop read; found 2026-09-08 building the live line,
    which can only ever know yesterday's ATR); prevd[d] is yesterday's
    bar with its own range_ratio and direction. One definition for the
    study and the 9:46 ping."""
    atr, prevd = {}, {}
    trs = []
    for i, (d, o, h, l, cl) in enumerate(daily):
        if i > 0:
            pd_ = daily[i - 1]
            if len(trs) >= 20:
                atr[d] = sum(trs[-20:]) / 20.0
            prev_atr = atr.get(d)          # through yesterday's close: one ATR per day
            prevd[d] = dict(open=pd_[1], high=pd_[2], low=pd_[3], close=pd_[4],
                            range_ratio=((pd_[2] - pd_[3]) / prev_atr) if prev_atr else None,
                            dir=("up" if i >= 2 and pd_[4] > daily[i - 2][4] else "down" if i >= 2 else None))
            trs.append(true_range(h, l, pd_[4]))
    return atr, prevd


# ── seeder ───────────────────────────────────────────────────────────

def _process_ticker(conn, tk, et):
    from zoneinfo import ZoneInfo
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, open, high, low, close FROM daily_prices
                     WHERE ticker=%s AND open IS NOT NULL AND close IS NOT NULL ORDER BY trade_date""", (tk,))
        daily = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in c.fetchall()]
        c.execute("""SELECT trade_date, ts, open, high, low, close FROM index_intraday_bars
                     WHERE ticker=%s ORDER BY ts""", (tk,))
        rows = c.fetchall()
        c.execute("SELECT as_of, vix, vix3m FROM vix_history")
        vix = {r[0]: {"vix": float(r[1]), "vix3m": float(r[2]) if r[2] is not None else None} for r in c.fetchall()}
        c.execute("SELECT as_of, regime, gamma_flip FROM gex_levels WHERE ticker=%s", (tk,))
        gamma = {r[0]: {"regime": r[1], "flip": float(r[2]) if r[2] is not None else None} for r in c.fetchall()}
    by_day = {}
    for d, ts, o, h, l, cl in rows:
        t = ts.astimezone(et)
        if dt.time(9, 30) <= t.time() < dt.time(16, 0):
            by_day.setdefault(d, []).append((t, float(o), float(h), float(l), float(cl)))
    atr, prevd = daily_facts(daily)
    n = 0
    with conn.cursor() as c:
        for d, o, h, l, cl in daily:
            bars = by_day.get(d)
            if not bars or d not in atr or d not in prevd or len(bars) < 4:
                continue
            lab, rr, cp = label_day(o, h, l, cl, atr[d])
            if lab is None:
                continue
            # prior-close VIX: the last vix row strictly before d (no lookahead)
            feats = {}
            for tcut, key in CHECKPOINTS:
                seen = [b for b in bars if (b[0] + dt.timedelta(minutes=15)).time() <= tcut]
                feats[key] = features(seen, prevd[d], atr[d], vix_row=_vix_before(vix, d),
                                      gamma=gamma.get(d) if d >= GAMMA_FROM else None, weekday=d.strftime("%a"))
            c.execute("""INSERT INTO daytype_days (ticker, trade_date, era, label, range_ratio, close_pos, day_ret_pct,
                                                   f945, f1000, f1030)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
                         ON CONFLICT (ticker, trade_date) DO NOTHING""",
                      (tk, d, "pre2016" if d < dt.date(2016, 1, 1) else "post2016", lab, rr, cp,
                       round((cl / o - 1) * 100, 3), json.dumps(feats["f945"]), json.dumps(feats["f1000"]),
                       json.dumps(feats["f1030"])))
            n += 1
    conn.commit()
    return n


_VIX_CACHE = {}


def _vix_before(vix, d):
    """The last VIX row strictly before d (the prior close's read)."""
    if not vix:
        return None
    keys = _VIX_CACHE.get("keys")
    if keys is None or _VIX_CACHE.get("n") != len(vix):
        keys = sorted(vix)
        _VIX_CACHE.update(keys=keys, n=len(vix))
    import bisect
    i = bisect.bisect_left(keys, d) - 1
    return vix[keys[i]] if i >= 0 else None


def run() -> bool:
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT ticker FROM daytype_progress")
            done = {r[0] for r in c.fetchall()}
        for tk in TICKERS:
            if tk in done:
                continue
            try:
                n = _process_ticker(conn, tk, et)
            except Exception as e:
                conn.rollback()
                log.warning("[daytype] %s failed: %s", tk, str(e)[:300])
                return False
            with conn.cursor() as c:
                c.execute("INSERT INTO daytype_progress (ticker, n_days) VALUES (%s,%s) ON CONFLICT DO NOTHING", (tk, n))
            conn.commit()
            log.info("[daytype] %s: %d days labeled", tk, n)
        with conn.cursor() as c:
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                      (COMPLETE_MARKER,))
        conn.commit()
        log.info("[daytype] complete.")
        return True
    finally:
        conn.close()


def run_append() -> int:
    """The table's OWNER after the seed (2026-09-09: daytype_days sat
    frozen at 2026-09-04 while the 📐 line read its priors from it —
    the marker-retired seeder never ran again; every stored record
    needs an owning job). Re-runs the SAME _process_ticker over the
    whole record: ON CONFLICT DO NOTHING keeps every labeled day, so
    only days that gained their bars since the last pass are added.
    16:45 ET — after the 16:20 index-bars append AND the 16:35 close sync
    that writes the day's daily_prices row (the label needs both; at
    16:32 it ran before the daily bar existed and wrote nothing, 9/11
    the day it was caught) — plus a boot catch-up. Returns rows written
    (0 on a current table is data, not a fault)."""
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    try:
        written = 0
        for tk in TICKERS:
            with conn.cursor() as c:
                c.execute("SELECT count(*) FROM daytype_days WHERE ticker=%s", (tk,))
                before = c.fetchone()[0]
            try:
                _process_ticker(conn, tk, et)
            except Exception as e:
                conn.rollback()
                log.warning("[daytype] append %s failed: %s", tk, str(e)[:300])
                continue
            with conn.cursor() as c:
                c.execute("SELECT count(*), max(trade_date) FROM daytype_days WHERE ticker=%s", (tk,))
                after, last = c.fetchone()
            written += after - before
            log.info("[daytype] %s: +%d day(s), labeled through %s", tk, after - before, last)
        return written
    finally:
        conn.close()


# ── the live read ───────────────────────────────────────────────────

def live_context(conn, ticker, today):
    """What the 9:46 line may know before the first bar: yesterday's bar
    with its range_ratio / direction, the ATR20 through yesterday's close,
    the prior-close VIX row and today's gamma board row — every one
    computed by the SAME functions the study used (daily_facts,
    _vix_before), so the live read and the graded record cannot drift.
    Missing pieces are None (holes), never defaults."""
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, open, high, low, close FROM daily_prices
                     WHERE ticker=%s AND trade_date < %s AND open IS NOT NULL AND close IS NOT NULL
                     ORDER BY trade_date DESC LIMIT 40""", (ticker, today))
        daily = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in reversed(c.fetchall())]
        c.execute("SELECT as_of, vix, vix3m FROM vix_history WHERE as_of < %s ORDER BY as_of DESC LIMIT 1", (today,))
        r = c.fetchone()
        vix_row = {"vix": float(r[1]), "vix3m": float(r[2]) if r[2] is not None else None} if r else None
        c.execute("SELECT regime, gamma_flip FROM gex_levels WHERE ticker=%s AND as_of=%s", (ticker, today))
        r = c.fetchone()
        gamma = {"regime": r[0], "flip": float(r[1]) if r[1] is not None else None} if r else None
    if not daily:
        return None, None, vix_row, gamma
    # a placeholder row for today lets daily_facts key its prior-close
    # facts by today's date; the placeholder's own values are never read
    pc = daily[-1][4]
    atr, prevd = daily_facts(daily + [(today, pc, pc, pc, pc)])
    return prevd.get(today), atr.get(today), vix_row, gamma


def prior(conn, ticker, checkpoint_key, state):
    """P(chop) / P(trend) / P(green | trend) for the days whose checkpoint
    read matched `state` on the given legs, era-split, n beside. state:
    {leg: value} — only the legs given are matched. Returns rows of
    (era, n, p_chop, p_trend, p_green_of_trend) or [] when nothing
    matched; the caller renders small-n and holes."""
    conds, args = [], [ticker]
    for k, v in state.items():
        if v is None:
            continue
        conds.append(f"{checkpoint_key}->>%s = %s")
        args += [k, str(v).lower() if isinstance(v, bool) else str(v)]
    where = " AND ".join(conds) if conds else "true"
    with conn.cursor() as c:
        c.execute(f"""SELECT era, count(*),
                             round(100.0 * avg((label='chop')::int), 1),
                             round(100.0 * avg((label IN ('trend_green','trend_red'))::int), 1),
                             round(100.0 * avg((label='trend_green')::int) / NULLIF(avg((label IN ('trend_green','trend_red'))::int), 0), 1)
                      FROM daytype_days WHERE ticker=%s AND {where} GROUP BY era ORDER BY era""", args)
        return c.fetchall()


READOUT_SQL = """
SELECT ticker, cp, leg, val, era, n,
       round(100.0 * chop / n, 1) AS p_chop, round(100.0 * trend / n, 1) AS p_trend,
       CASE WHEN trend > 0 THEN round(100.0 * green / trend, 1) END AS p_green_of_trend
FROM (
  SELECT ticker, cp, leg, val, era, count(*) AS n,
         count(*) FILTER (WHERE label='chop') AS chop,
         count(*) FILTER (WHERE label IN ('trend_green','trend_red')) AS trend,
         count(*) FILTER (WHERE label='trend_green') AS green
  FROM daytype_days d,
       LATERAL (VALUES ('f945', d.f945), ('f1000', d.f1000), ('f1030', d.f1030)) AS c(cp, f),
       LATERAL jsonb_each_text(c.f) AS kv(leg, val)
  GROUP BY ticker, cp, leg, val, era) x
WHERE n >= 40
ORDER BY ticker, cp, leg, val, era
"""
