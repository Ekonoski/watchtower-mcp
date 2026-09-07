"""
The Darvas Box study (2026-09-05, Eric: "back test the Nicolas Darvas
box theory and tell me its win results and profit").

Pre-registered spec, frozen before any number is computed:

  universe   currently-listed names with >= 500 stored daily bars whose
             trailing-90-day average dollar volume is >= $10M today
             (survivorship: the corpses are unseen — stated at readout);
             per bar, a box may only START when close >= $5 and the
             50-day average dollar volume >= $5M (no lookahead on
             liquidity)
  box        Darvas's own rule, on daily bars: a NEW 52-WEEK HIGH
             (252 bars) opens a box; the TOP is that high once three
             consecutive bars fail to exceed it (a higher high resets
             the top); the BOTTOM is the lowest low since the top bar
             once three consecutive bars fail to undercut it (a new high
             before the bottom sets restarts the top). A close below
             the bottom before entry invalidates the box.
  entry      the first CLOSE above the box top (wick rule — a wick
             through is not a breakout), at that close. Breakout-close
             entries: stated wherever the numbers surface.
  stop       the box bottom. Exits are graded THREE ways on the SAME
             entries: trail_close (Darvas classic — the stop rises to the
             bottom of each new completed box above the last; exit on a
             CLOSE below the stop), trail_touch (same trail, exit at the
             stop on a touch), box_close (initial stop only, never
             trailed). Every variant caps the hold at 250 bars (exit at
             that close, reason max_hold) so a trend-follower's open
             trade at the record's end is a HOLE, never a number.
  control    random_close — for every real trade, a random bar of the
             same ticker (seeded by ticker, reproducible) with the SAME
             stop distance (the box height %), close-stop, 250-bar cap:
             the same geometry with the breakout condition removed.
  outcomes   r on the box risk (entry − bottom), capped ±10 at readout
             (the outlier lesson); % return; hold; MFE/MAE in R; exit
             reason; vol_confirm (breakout volume >= 1.5x the 50-day
             average) and spy_above_200 (SPY close above its 200-day at
             entry) stamped as CUTS, never gates; at_ath (the box top was
             an all-time high of the STORED record — shallow histories
             make this weaker, stated); era pre/post-2016.
  bar        both eras positive AND per-year replication; no costs, no
             slippage, no dividends, closes as fills. "Profit" is
             reported as expectancy per trade and as a 1%-risk-per-trade
             equity path in chronological order, with its drawdown.

Chunked fleet seeder (resume via darvas_progress; marker darvas_v1 when
the universe is processed). Reads daily_prices_clean, writes only its
own tables. The box machine is pure and tested.
"""
import datetime as dt
import logging
import random

log = logging.getLogger("watchtower.darvas")

COMPLETE_MARKER = "darvas_v1"
LOOKBACK = 252            # 52-week high
CONFIRM = 3               # bars that must fail to exceed / undercut
MAX_HOLD = 250
MIN_ROWS = 500
MIN_PX = 5.0
MIN_DV50 = 5e6
UNIVERSE_DV90 = 10e6
VOL_CONFIRM = 1.5
VARIANTS = ("trail_close", "trail_touch", "box_close")


# ── pure: the box machine ──────────────────────────────────────────────

def find_boxes(highs, lows, closes, ok):
    """Pure. Darvas boxes on daily bars. `ok[i]` says whether a box may
    START at bar i (price/liquidity gate, no lookahead). Returns a list
    of dicts: top, bottom, top_bar, ready_bar, entry_bar (first close >
    top after ready) or None if the box was invalidated (close < bottom)
    or the record ended. A box that yields an entry ends the search
    until the trade's exit is decided by the caller (the caller passes
    `start` to resume)."""
    return _boxes_from(highs, lows, closes, ok, 0, len(highs), None)


def _boxes_from(highs, lows, closes, ok, start, end, floor_high):
    """Box search over [start, end). floor_high: when set (trail mode),
    a box only opens on a high ABOVE it instead of a 52-week high."""
    out = []
    i = max(start, LOOKBACK if floor_high is None else start)
    state = "seek"
    H = L = None
    top_bar = None
    cnt = 0
    while i < end:
        h, l, c = highs[i], lows[i], closes[i]
        if state == "seek":
            new_high = (h > floor_high) if floor_high is not None else \
                (i >= LOOKBACK and h >= max(highs[i - LOOKBACK:i]) and ok[i])
            if new_high:
                H, top_bar, cnt, state = h, i, 0, "top"
        elif state == "top":
            if h > H:
                H, top_bar, cnt = h, i, 0
            else:
                cnt += 1
                if cnt == CONFIRM:
                    L = min(lows[top_bar:i + 1])
                    cnt, state = 0, "bottom"
        elif state == "bottom":
            if h > H:
                H, top_bar, cnt, state = h, i, 0, "top"
            elif l < L:
                L, cnt = l, 0
            else:
                cnt += 1
                if cnt == CONFIRM:
                    state = "ready"
                    box = dict(top=H, bottom=L, top_bar=top_bar, ready_bar=i, entry_bar=None)
        elif state == "ready":
            if c > H:
                box["entry_bar"] = i
                out.append(box)
                return out            # caller decides the trade, then resumes
            if c < L:
                out.append(box)       # invalidated: recorded, no entry
                state = "seek"
        i += 1
    if state == "ready":
        out.append(box)               # record ended with a live box (no entry)
    return out


def simulate(highs, lows, closes, entry_bar, top, bottom, variant, end=None):
    """Pure. One trade from entry_bar under a variant. Returns dict with
    exit_bar, exit_px, reason, stop_final, mfe_r, mae_r; reason
    'record_end' when the record runs out before an exit (a hole)."""
    n = len(closes) if end is None else end
    entry = closes[entry_bar]
    risk = entry - bottom
    if risk <= 0:
        return None
    stop = bottom
    mfe = mae = 0.0
    trail = variant.startswith("trail")
    touch = variant == "trail_touch"
    # trail tracker: a new box must open on a high above the current box top
    tH, tL, t_top_bar, t_cnt, t_state = top, None, None, 0, "seek"
    cur_top = top
    for j in range(entry_bar + 1, min(n, entry_bar + MAX_HOLD + 1)):
        h, l, c = highs[j], lows[j], closes[j]
        mfe = max(mfe, (c - entry) / risk)
        mae = min(mae, (c - entry) / risk)
        if touch and l <= stop:
            return dict(exit_bar=j, exit_px=stop, reason="trail_stop" if stop > bottom else "stop_touch",
                        stop_final=stop, mfe_r=mfe, mae_r=mae)
        if not touch and c < stop:
            return dict(exit_bar=j, exit_px=c, reason="trail_stop" if stop > bottom else "stop_close",
                        stop_final=stop, mfe_r=mfe, mae_r=mae)
        if trail:
            if t_state == "seek":
                if h > cur_top:
                    tH, t_top_bar, t_cnt, t_state = h, j, 0, "top"
            elif t_state == "top":
                if h > tH:
                    tH, t_top_bar, t_cnt = h, j, 0
                else:
                    t_cnt += 1
                    if t_cnt == CONFIRM:
                        tL, t_cnt, t_state = min(lows[t_top_bar:j + 1]), 0, "bottom"
            elif t_state == "bottom":
                if h > tH:
                    tH, t_top_bar, t_cnt, t_state = h, j, 0, "top"
                elif l < tL:
                    tL, t_cnt = l, 0
                else:
                    t_cnt += 1
                    if t_cnt == CONFIRM:
                        if tL > stop:
                            stop = tL
                        cur_top, t_state = tH, "seek"
        if j - entry_bar >= MAX_HOLD:
            return dict(exit_bar=j, exit_px=c, reason="max_hold", stop_final=stop, mfe_r=mfe, mae_r=mae)
    return dict(exit_bar=None, exit_px=None, reason="record_end", stop_final=stop, mfe_r=mfe, mae_r=mae)


def trades_for_ticker(highs, lows, closes, ok):
    """Pure. Walk the record: box -> entry -> (trail_close decides where
    the search resumes, so overlapping entries never occur) -> next box.
    Returns [(box, {variant: sim})]."""
    out, start = [], 0
    n = len(closes)
    while start < n:
        boxes = _boxes_from(highs, lows, closes, ok, start, n, None)
        if not boxes:
            break
        box = boxes[-1]
        if box["entry_bar"] is None:
            break
        sims = {v: simulate(highs, lows, closes, box["entry_bar"], box["top"], box["bottom"], v) for v in VARIANTS}
        out.append((box, sims))
        ref = sims["trail_close"]
        start = (ref["exit_bar"] if ref["exit_bar"] is not None else n) + 1
    return out


# ── the seeder ─────────────────────────────────────────────────────────

def _spy_regime(conn):
    with conn.cursor() as c:
        c.execute("SELECT trade_date, close FROM daily_prices WHERE ticker='SPY' ORDER BY trade_date")
        rows = c.fetchall()
    out, window = {}, []
    for d, cl in rows:
        window.append(float(cl))
        if len(window) > 200:
            window.pop(0)
        out[d] = (len(window) == 200 and float(cl) > sum(window) / 200)
    return out


def _process_ticker(conn, ticker, spy_above):
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, COALESCE(high, close), COALESCE(low, close), close,
                            COALESCE(volume, 0)
                     FROM daily_prices_clean WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (ticker,))
        rows = c.fetchall()
    if len(rows) < MIN_ROWS:
        return 0
    dates = [r[0] for r in rows]
    highs = [float(r[1]) for r in rows]
    lows = [float(r[2]) for r in rows]
    closes = [float(r[3]) for r in rows]
    vols = [float(r[4]) for r in rows]
    dv = [c * v for c, v in zip(closes, vols)]
    ok, vol50 = [], []
    s_dv = s_v = 0.0
    for i in range(len(rows)):
        s_dv += dv[i]; s_v += vols[i]
        if i >= 50:
            s_dv -= dv[i - 50]; s_v -= vols[i - 50]
        avg_dv = s_dv / min(i + 1, 50)
        vol50.append(s_v / min(i + 1, 50))
        ok.append(i >= 50 and closes[i] >= MIN_PX and avg_dv >= MIN_DV50)
    trades = trades_for_ticker(highs, lows, closes, ok)
    rng = random.Random(ticker)
    n = 0
    with conn.cursor() as c:
        for box, sims in trades:
            eb = box["entry_bar"]
            entry = closes[eb]
            risk = entry - box["bottom"]
            height = (box["top"] - box["bottom"]) / box["top"] * 100
            vol_ok = vols[eb] >= VOL_CONFIRM * vol50[eb] if vol50[eb] > 0 else None
            at_ath = box["top"] >= max(highs[:box["top_bar"] + 1])
            era = "pre2016" if dates[eb] < dt.date(2016, 1, 1) else "post2016"
            common = (ticker, dates[eb], entry, box["top"], box["bottom"], round(height, 2),
                      vol_ok, spy_above.get(dates[eb]), at_ath, era, round(dv[eb], 0))
            for v, s in sims.items():
                c.execute(_INSERT, common + (v,) + _outcome(s, dates, entry, risk, eb))
                n += 1
            # control: random bar, same stop distance, close-stop, 250-bar cap
            lo, hi = LOOKBACK, len(closes) - 2
            if hi > lo:
                rb = rng.randint(lo, hi)
                r_entry = closes[rb]
                r_bottom = r_entry * (1 - height / 100)
                s = simulate(highs, lows, closes, rb, r_entry, r_bottom, "box_close")
                if s is not None:
                    c.execute(_INSERT, (ticker, dates[rb], r_entry, None, r_bottom, round(height, 2),
                                        None, spy_above.get(dates[rb]), None,
                                        "pre2016" if dates[rb] < dt.date(2016, 1, 1) else "post2016",
                                        round(dv[rb], 0), "random_close")
                              + _outcome(s, dates, r_entry, r_entry - r_bottom, rb))
                    n += 1
    conn.commit()
    return n


_INSERT = """INSERT INTO darvas_events
    (ticker, entry_date, entry_px, box_top, box_bottom, box_height_pct, vol_confirm,
     spy_above_200, at_ath, era, dollar_vol, variant,
     exit_date, exit_px, exit_reason, hold_days, r, pct, mfe_r, mae_r)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (ticker, entry_date, variant) DO NOTHING"""


def _outcome(s, dates, entry, risk, eb):
    if s is None or s["exit_bar"] is None:
        return (None, None, "record_end", None, None, None,
                round(s["mfe_r"], 3) if s else None, round(s["mae_r"], 3) if s else None)
    xb = s["exit_bar"]
    return (dates[xb], s["exit_px"], s["reason"], xb - eb,
            round((s["exit_px"] - entry) / risk, 4), round((s["exit_px"] / entry - 1) * 100, 3),
            round(s["mfe_r"], 3), round(s["mae_r"], 3))


def run(batch: int = 300) -> bool:
    """Process up to `batch` unprocessed universe tickers; True when the
    universe is done (marker claimed)."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("""WITH recent AS (
                           SELECT ticker, avg(close*volume) AS dv FROM daily_prices
                           WHERE trade_date >= CURRENT_DATE - 90 GROUP BY ticker),
                         hist AS (SELECT ticker, count(*) AS days FROM daily_prices GROUP BY ticker)
                         SELECT r.ticker FROM recent r JOIN hist h USING (ticker)
                         WHERE r.dv >= %s AND h.days >= %s
                           AND NOT EXISTS (SELECT 1 FROM darvas_progress p WHERE p.ticker = r.ticker)
                         ORDER BY r.ticker LIMIT %s""", (UNIVERSE_DV90, MIN_ROWS, batch))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                          "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
            conn.commit()
            log.info("[darvas] universe complete; marker claimed.")
            return True
        spy_above = _spy_regime(conn)
        for tk in todo:
            try:
                n = _process_ticker(conn, tk, spy_above)
            except Exception as e:
                conn.rollback()
                log.warning("[darvas] %s failed: %s", tk, str(e)[:300])
                n = 0
            with conn.cursor() as c:
                c.execute("INSERT INTO darvas_progress (ticker, n_events) VALUES (%s,%s) "
                          "ON CONFLICT (ticker) DO NOTHING", (tk, n))
            conn.commit()
        log.info("[darvas] processed %d ticker(s) this pass.", len(todo))
        return False
    finally:
        conn.close()
