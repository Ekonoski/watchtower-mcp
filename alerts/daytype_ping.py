"""
The 📐 DAY-TYPE line (2026-09-08, Eric: "we need to identify chop vs a
red or green day" → "why will I only know at 10:30?" — he won't: the
day-type study's 9:45 read already sorts the day).

Three posts to #desk per trading day, one per study checkpoint, each
the live read of the SAME features the study graded (analysis/
daytype_study.features, one definition) against the SAME record
(daytype_study.prior — 9,300 SPY/QQQ days, era-split, n beside):

  9:46   yesterday's range vs ATR × the first 15m bar's range vs ATR —
         the two-leg grid that was monotone in every cell, both eras,
         both tickers (tight × tight → chop ~80%; wide × wide → trend
         ~50%).
  10:01  the same legs with the 30-minute opening range.
  10:31  the first hour's range and whether a bar CLOSED through the
         30-minute range (wick rule).

Measurement only: no book reads this line, no rule changes on it. Small
n (<40 days) renders as small n; a missing input renders as a hole,
never a default. At-most-once per (kind, date) via discord_notify_log
claims. The module reads the record and Polygon only — by signature it
cannot write paper_specs / paper_trades / the journal
(tests/test_daytype_ping.py).
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.daytype_ping")

CHANNEL = "desk"
TICKERS = ("SPY", "QQQ")
SMALL_N = 40
# (checkpoint time, study key, discord kind, the legs matched at that read)
CHECKPOINTS = (
    (dt.time(9, 45), "f945", "daytype_945", ("prev_range_ratio", "orb_ratio")),
    (dt.time(10, 0), "f1000", "daytype_1000", ("prev_range_ratio", "orb_ratio")),
    (dt.time(10, 30), "f1030", "daytype_1030", ("orb_ratio", "orb_break")),
)
GRACE = dt.timedelta(minutes=12)   # a checkpoint is announced within this window, then left to the next


def _pct(x):
    return "—" if x is None else f"{float(x):.0f}%"


def _ratio(x, atr=None):
    """'0.71 ATR ($3.71)' — the yardstick and the dollars beside it (Eric,
    2026-09-08: "how would I know what an ATR is?")."""
    if x is None:
        return "unavailable"
    s = f"{float(x):.2f} ATR"
    return s + (f" (${float(x) * float(atr):.2f})" if atr else "")


RANGE_AT = 65.0      # pooled chop ≥ this → RANGE LIKELY   (labels on the number, not a rule — Eric, 2026-09-08)
TRAVEL_AT = 35.0     # pooled chop ≤ this → TRAVEL LIKELY
ERA_AGREE = 10.0     # eras within this many points agree; otherwise the verdict is UNDECIDED
HUG_PCT = 0.3        # open within this % of the flip = the blender read (flipprox: 1.3–1.5 crosses/day)


def verdict(rows):
    """Pure. rows = prior() rows [(era, n, p_chop, p_trend, p_green_of_trend)].
    Pools the eras into ONE number and runs the desk's both-eras check as a
    flag: returns (word, n, chop, trend, green_of_trend, flag) where word is
    RANGE LIKELY / TRAVEL LIKELY / UNDECIDED / NO READ and flag names why a
    verdict was withheld (eras disagree, small n) or 'eras agree'. Small n
    and disagreement never render as a confident word."""
    rows = [r for r in rows if r and r[1]]
    if not rows:
        return "NO READ", 0, None, None, None, "no matching days"
    n = sum(r[1] for r in rows)
    chop = sum(float(r[2]) * r[1] for r in rows) / n
    trend = sum(float(r[3]) * r[1] for r in rows) / n
    gt = [(float(r[4]), r[1]) for r in rows if r[4] is not None]
    green = (sum(g * k for g, k in gt) / sum(k for _, k in gt)) if gt else None
    if n < SMALL_N:
        return "UNDECIDED", n, chop, trend, green, "⚠ small n"
    chops = [float(r[2]) for r in rows]
    if len(rows) >= 2 and max(chops) - min(chops) > ERA_AGREE:
        return "UNDECIDED", n, chop, trend, green, f"⚠ eras disagree ({' / '.join(f'{c:.0f}' for c in chops)})"
    flag = "eras agree" if len(rows) >= 2 else "one era"
    word = "RANGE LIKELY" if chop >= RANGE_AT else "TRAVEL LIKELY" if chop <= TRAVEL_AT else "UNDECIDED"
    return word, n, chop, trend, green, flag


def format_read(ticker, cp_key, feats, rows, raw):
    """Pure: one ticker, ONE line. feats = features() output (may be {}),
    rows = prior() rows, raw = {'prev_rr', 'orb_rr', 'open_state',
    'vix_backwardated', 'gamma_regime', 'flip_pct'}."""
    tcut = {"f945": "9:45", "f1000": "10:00", "f1030": "10:30"}[cp_key]
    head = f"📐 **DAY TYPE** {tcut} · **{ticker}**"
    if not feats:
        return f"{head} *unavailable* — no first bar / no daily context (a hole, not a read)"
    word, n, chop, trend, green, flag = verdict(rows)
    # direction rides the 10:30 break only: the break's job is COLOR, not trend
    color = ""
    if cp_key == "f1030" and word == "TRAVEL LIKELY" and green is not None and feats.get("orb_break") in ("up", "down"):
        color = ", green favored" if green >= 60 else ", red favored" if green <= 40 else ""
    numbers = ("no matching days" if chop is None else
               f"chop {_pct(chop)} · trend {_pct(trend)} (n={n:,}, {flag})")
    atr = raw.get("atr")
    legs = [f"ydy {_ratio(raw.get('prev_rr'), atr)}",
            f"{'first bar' if cp_key == 'f945' else '30m range' if cp_key == 'f1000' else 'first hour'} {_ratio(raw.get('orb_rr'), atr)}"]
    if atr:
        legs.append(f"ATR ${float(atr):.2f}")
    if cp_key == "f1030":
        ob = feats.get("orb_break")
        g = f" (green {_pct(green)} of trends)" if green is not None and ob in ("up", "down") else ""
        legs.append("no close through the 30m range" if ob == "none" else
                    f"closed {ob} through the 30m range{g}" if ob else "30m break unavailable")
    ctx = []
    if raw.get("open_state"):
        ctx.append({"above_pdh": "open > PDH", "below_pdl": "open < PDL", "inside": "open inside"}[raw["open_state"]])
    vb = raw.get("vix_backwardated")
    ctx.append("VIX backwardated" if vb else "VIX contango" if vb is False else "VIX unavailable")
    fp = raw.get("flip_pct")
    if fp is not None:
        ctx.append(f"flip {fp:.2f}% away" + (" ⚠ hugging" if fp < HUG_PCT else ""))
    elif raw.get("gamma_regime"):
        ctx.append(f"gamma {raw['gamma_regime']}")
    return f"{head} **{word}{color}** — {numbers} · {' · '.join(legs)} · {' · '.join(ctx)}"


def format_post(cp_key, tcut, lines):
    """One line per index; the footnote rides only the 9:45 post so the
    phone sees the definition once a day."""
    body = "\n".join(lines)
    if cp_key == "f945":
        body += ("\n_Priors: SPY 2005→ / QQQ 2011→, labeled at the close (chop = range < 0.75 ATR or mid close; "
                 "trend = ≥0.9 ATR closing in its top/bottom quarter). Measurement only — no book trades this line._")
    return body


def _rth_bars(client, ticker, today, tcut, et):
    """Completed RTH 15m bars by tcut, as (ts, o, h, l, c) — a bar counts
    only once its END time is at/before the checkpoint (a forming bar is
    never read as a completed one)."""
    aggs = list(client.get_aggs(ticker, multiplier=15, timespan="minute",
                                from_=today.isoformat(), to=today.isoformat(), limit=200))
    out = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000, dt.timezone.utc).astimezone(et)
        if dt.time(9, 30) <= t.time() < dt.time(16, 0) and (t + dt.timedelta(minutes=15)).time() <= tcut:
            out.append((t, float(a.open), float(a.high), float(a.low), float(a.close)))
    return out


def run_daytype_ping() -> dict:
    """Post every due, unclaimed checkpoint read for SPY and QQQ."""
    from alerts.discord_notify import claim_and_send, is_configured
    from analysis.daytype_study import features, live_context, prior
    from analysis.paper_trader import ET
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn

    now = dt.datetime.now(ET)
    if now.weekday() >= 5:
        return {"skip": "weekend"}
    if not is_configured(CHANNEL):
        return {"off": True}
    today = now.date()
    due = [(t, k, kind, legs) for t, k, kind, legs in CHECKPOINTS
           if t <= now.time() <= (dt.datetime.combine(today, t) + GRACE).time()]
    if not due:
        return {"skip": "no checkpoint due"}
    client = get_client()
    if client is None:
        return {"skip": "no polygon client"}
    conn = _conn()
    res = {}
    try:
        for tcut, key, kind, legs in due:
            with conn.cursor() as c:
                c.execute("SELECT 1 FROM discord_notify_log WHERE kind=%s AND ref=%s", (kind, today.isoformat()))
                if c.fetchone():
                    res[kind] = "duplicate"
                    continue
            lines = []
            for tk in TICKERS:
                try:
                    prev, atr, vix_row, gamma = live_context(conn, tk, today)
                    bars = _rth_bars(client, tk, today, tcut, ET)
                except Exception as e:
                    log.warning("[daytype-ping] %s context failed: %s", tk, str(e)[:300])
                    prev, atr, vix_row, gamma, bars = None, None, None, None, []
                feats = features(bars, prev, atr, vix_row=vix_row, gamma=gamma, weekday=today.strftime("%a"))
                rows = prior(conn, tk, key, {lg: feats.get(lg) for lg in legs}) if feats else []
                raw = {}
                if feats:
                    hi, lo = max(b[2] for b in bars), min(b[3] for b in bars)
                    raw = {"prev_rr": prev.get("range_ratio"), "orb_rr": (hi - lo) / atr, "atr": atr,
                           "open_state": feats.get("open_state"), "vix_backwardated": feats.get("vix_backwardated"),
                           "gamma_regime": feats.get("gamma_regime"),
                           "flip_pct": (abs(bars[0][1] - gamma["flip"]) / bars[0][1] * 100)
                           if gamma and gamma.get("flip") else None}
                lines.append(format_read(tk, key, feats, rows, raw))
            res[kind] = claim_and_send(kind, today.isoformat(), CHANNEL, format_post(key, tcut, lines), conn=conn)
        return res
    finally:
        conn.close()
