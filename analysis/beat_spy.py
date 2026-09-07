"""
The Beat-SPY challenge harness (2026-09-07, Eric: "find and create a
trading system that beats the SPY… you are completely free… options
allowed"). Rules for both contestants: docs/research/beat_spy_challenge_rules.md.

This module is a DAILY portfolio simulator over the stored daily record
plus the system under test ("Compass"). Everything is pre-registered in
code; the build window is data through 2023-12-31; the sealed window
(2024-01-02 → 2026-09-04) runs ONCE on frozen rules and never again.

System "Compass" (v1, shares only; v1.1 adds the defined-risk option
overlay on the same signals):

  Regime     SPY close > its 200-day SMA = risk-on. Otherwise risk-off:
             the trend sleeve holds the DEFENSIVE pool (TLT, GLD, cash)
             by the same momentum rule with cash as a candidate at 0%.
  Sleeve A   ETF trend, weekly (Friday close): rank the ETF pool by
   (trend)   momentum = 126-day return minus 21-day return (the last
             month excluded, the standard anti-reversal), require close
             above the 200-day SMA, hold the top N equal-weight, exit
             when a holding falls out of the top 2N or closes below its
             200-day. Weight: A_WEIGHT of equity.
  Sleeve B   Washout ladder from the stored 16D green-dot record
   (dots)    (greendot_dots — fixed-anchor blocks, no repaint): a dot
             with cross depth <= -30 and drawdown >= 50% on a name that
             is liquid today (survivorship STATED) and >= $2 at the dot
             opens a 3-tranche ladder — 1/3 at the dot close, 1/3 at
             -15%, 1/3 at -25% — held 126 trading days from the dot,
             then sold at the close. Max B_SLOTS open ladders; each
             full ladder = B_RISK of equity.
  Costs      5 bps per side on every share fill. Cash earns 0%.
  Benchmark  SPY total return with dividends reinvested (Polygon
             dividends; cash amounts on ex-date).

Pure functions carry the rules; run_variant() simulates; results go to
beat_spy_runs / beat_spy_equity / beat_spy_trades. Nothing here touches
a live book.
"""
import datetime as dt
import json
import logging
import math

log = logging.getLogger("watchtower.beat_spy")

COST_BPS = 5.0
ETF_POOL = ("SPY", "QQQ", "IWM", "MDY", "DIA", "RSP", "XLK", "XLF", "XLV", "XLB", "XLE",
            "XLI", "XLP", "XLU", "XLY", "XLRE", "XLC", "XBI", "IBB", "SMH", "SOXX", "XHB",
            "ITB", "XME", "XOP", "OIH", "KRE", "KBE", "XRT", "IYT", "VNQ", "GDX", "GDXJ",
            "FXI", "KWEB", "IHI", "IGV", "ITA", "TAN", "ICLN", "URA", "LIT", "ARKK", "HYG",
            "TLT", "GLD")
DEFENSIVE_POOL = ("TLT", "GLD")
BUILD_END = dt.date(2023, 12, 31)
SEALED_START, SEALED_END = dt.date(2024, 1, 2), dt.date(2026, 9, 4)

DEFAULTS = dict(a_weight=0.70, top_n=5, mom_long=126, mom_skip=21, sma=200,
                b_weight=0.20, b_slots=10, b_hold=126, b_depth=-30.0, b_dd=50.0,
                ladder=(0.0, -0.15, -0.25), start=dt.date(2006, 1, 3), end=BUILD_END,
                # v1: weekly SPY-vs-200d regime that liquidates the trend sleeve on
                # risk-off. v2 (2026-09-07, after the first build read — 172 forced
                # 'risk_off' exits at 31% win, −$64k): regime='faber' checks SPY
                # against its 10-MONTH average on month-end closes only, and the
                # regime governs NEW entries — holdings leave on their own 200-day
                # or on falling out of the top 2N, never because SPY blinked.
                regime="weekly200", regime_months=10, force_liquidate=True)


def month_ends(dates):
    """Pure. Indices of the last trading day of each month."""
    return [i for i in range(len(dates)) if i + 1 == len(dates) or dates[i + 1].month != dates[i].month]


# ── pure rules ─────────────────────────────────────────────────────────

def sma(vals, n, i):
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def momentum(closes, i, long_n, skip_n):
    """Pure. (close[i-skip] / close[i-long]) - 1; None inside warmup."""
    if i - long_n < 0:
        return None
    a, b = closes[i - long_n], closes[i - skip_n]
    return (b / a - 1.0) if a > 0 else None


def rank_pool(cands):
    """Pure. cands: {ticker: momentum}. Sorted best-first, None dropped."""
    return [t for t, m in sorted(((t, m) for t, m in cands.items() if m is not None),
                                 key=lambda kv: -kv[1])]


def target_holdings(ranked, current, top_n):
    """Pure. Keep a current holding while it stays inside the top 2N;
    fill open slots from the top of the ranking."""
    keep = [t for t in current if t in ranked[:2 * top_n]]
    for t in ranked:
        if len(keep) >= top_n:
            break
        if t not in keep:
            keep.append(t)
    return keep[:top_n]


def ladder_fills(entry_px, lows, closes, offsets):
    """Pure. Given the dot close and the subsequent path (lows/closes
    AFTER the dot bar), return the tranche fill prices in order: tranche 0
    at the dot close, later tranches at their limit when a low prints
    through it. Unfilled tranches are None."""
    fills = [entry_px] + [None] * (len(offsets) - 1)
    for k in range(1, len(offsets)):
        lim = entry_px * (1 + offsets[k])
        for lo in lows:
            if lo <= lim:
                fills[k] = lim
                break
    return fills


def max_drawdown(equity):
    """Pure. Worst peak-to-trough fraction of an equity series."""
    peak, worst = -1e18, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            worst = min(worst, e / peak - 1.0)
    return worst


def cagr(e0, e1, days):
    yrs = days / 365.25
    return (e1 / e0) ** (1 / yrs) - 1 if yrs > 0 and e0 > 0 else None


# ── data ───────────────────────────────────────────────────────────────

def _ensure_history(conn, tickers, since=dt.date(2004, 12, 1)):
    """Research backfill (the VFF precedent): an ETF in the pool whose
    stored history starts after `since` gets its earlier daily bars
    fetched from Polygon into daily_prices (ON CONFLICT DO NOTHING).
    Missing years otherwise silently shrink the pool — a hole."""
    from analysis.polygon_data import get_client
    client = get_client()
    with conn.cursor() as c:
        c.execute("SELECT ticker, min(trade_date) FROM daily_prices WHERE ticker = ANY(%s) GROUP BY ticker",
                  (list(tickers),))
        have = dict(c.fetchall())
    for tk in tickers:
        first = have.get(tk)
        if first is not None and first <= since + dt.timedelta(days=45):
            continue
        if client is None:
            log.warning("[beat_spy] no Polygon client; %s history not extended (hole).", tk)
            continue
        try:
            aggs = list(client.list_aggs(tk, 1, "day", since.isoformat(),
                                         (first or dt.date.today()).isoformat(), limit=50000))
        except Exception as e:
            log.warning("[beat_spy] %s backfill failed: %s", tk, str(e)[:200])
            continue
        n = 0
        with conn.cursor() as c:
            for a in aggs:
                d = dt.datetime.fromtimestamp(a.timestamp / 1000, dt.timezone.utc).date()
                c.execute("""INSERT INTO daily_prices (ticker, trade_date, open, high, low, close, volume)
                             VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ticker, trade_date) DO NOTHING""",
                          (tk, d, a.open, a.high, a.low, a.close, a.volume))
                n += 1
        conn.commit()
        log.info("[beat_spy] %s: %d bars ensured back to %s", tk, n, since)


def _load(conn, tickers):
    out = {}
    with conn.cursor() as c:
        for tk in tickers:
            c.execute("""SELECT trade_date, COALESCE(high, close), COALESCE(low, close), close
                         FROM daily_prices_clean WHERE ticker=%s AND close IS NOT NULL ORDER BY trade_date""", (tk,))
            rows = c.fetchall()
            if rows:
                out[tk] = dict(dates=[r[0] for r in rows], highs=[float(r[1]) for r in rows],
                               lows=[float(r[2]) for r in rows], closes=[float(r[3]) for r in rows],
                               idx={r[0]: i for i, r in enumerate(rows)})
    return out


def _spy_dividends(conn):
    """SPY cash dividends by ex-date from Polygon (cached in beat_spy_dividends)."""
    with conn.cursor() as c:
        c.execute("SELECT ex_date, amount FROM beat_spy_dividends WHERE ticker='SPY'")
        rows = dict(c.fetchall())
    if rows:
        return {d: float(a) for d, a in rows.items()}
    from analysis.polygon_data import get_client
    client = get_client()
    if client is None:
        log.warning("[beat_spy] no Polygon client; SPY dividends unavailable (hole: benchmark is price return).")
        return {}
    out = {}
    try:
        for dv in client.list_dividends("SPY", limit=1000):
            d = dt.date.fromisoformat(dv.ex_dividend_date)
            out[d] = out.get(d, 0.0) + float(dv.cash_amount)
    except Exception as e:
        log.warning("[beat_spy] dividends fetch failed: %s", str(e)[:200])
        return {}
    with conn.cursor() as c:
        for d, a in out.items():
            c.execute("INSERT INTO beat_spy_dividends (ticker, ex_date, amount) VALUES ('SPY',%s,%s) "
                      "ON CONFLICT DO NOTHING", (d, a))
    conn.commit()
    return out


def _dots(conn, p):
    """Deep dots on names liquid today (>= $10M/day trailing 90d)."""
    with conn.cursor() as c:
        c.execute("""WITH liq AS (SELECT ticker FROM daily_prices WHERE trade_date >= CURRENT_DATE - 90
                                  GROUP BY ticker HAVING avg(close*volume) >= 10e6)
                     SELECT g.ticker, g.dot_date, g.px_at_dot FROM greendot_dots g JOIN liq USING (ticker)
                     WHERE g.cross_depth <= %s AND g.drawdown_pct >= %s AND g.px_at_dot >= 2
                     ORDER BY g.dot_date""", (p["b_depth"], p["b_dd"]))
        return c.fetchall()


# ── simulation ─────────────────────────────────────────────────────────

def simulate(px, dots, spy_div, p, capital=100_000.0):
    """One full run. px: {ticker: series}; dots: [(ticker, date, px)].
    Returns dict(equity=[(date, eq, spy_tr)], trades=[...], stats)."""
    cal = px["SPY"]["dates"]
    spy = px["SPY"]
    start_i = next(i for i, d in enumerate(cal) if d >= p["start"])
    end_i = max(i for i, d in enumerate(cal) if d <= p["end"])
    cost = COST_BPS / 1e4
    cash = capital
    pos = {}                 # ticker -> dict(sleeve, qty, entry, entry_date, ...)
    ladders = {}             # ticker -> dict(dot_date, dot_px, fills, qty, exit_i)
    trades, equity = [], []
    spy_units = capital / spy["closes"][start_i]
    spy_cash = 0.0
    dots_by_date = {}
    for tk, d, dpx in dots:
        dots_by_date.setdefault(d, []).append((tk, float(dpx)))
    # Faber regime: state changes only on month-end closes
    me_set = set(month_ends(cal))
    me_closes = []            # SPY month-end closes seen so far
    faber_on = True

    def price(tk, d):
        s = px.get(tk)
        if not s:
            return None
        i = s["idx"].get(d)
        return s["closes"][i] if i is not None else None

    def close_pos(tk, d, reason):
        nonlocal cash
        o = pos.pop(tk)
        p_ = price(tk, d)
        if p_ is None:
            p_ = o["last"]
        proceeds = o["qty"] * p_ * (1 - cost)
        cash += proceeds
        trades.append(dict(sleeve=o["sleeve"], ticker=tk, entry_date=o["entry_date"], entry_px=o["entry"],
                           exit_date=d, exit_px=p_, qty=o["qty"], pnl=proceeds - o["cost_basis"], reason=reason))

    def open_pos(tk, d, dollars, sleeve, reason=""):
        nonlocal cash
        p_ = price(tk, d)
        if p_ is None or dollars <= 0 or dollars > cash:
            return False
        qty = dollars / (p_ * (1 + cost))
        cash -= dollars
        if tk in pos:
            o = pos[tk]
            o["cost_basis"] += dollars
            o["entry"] = (o["entry"] * o["qty"] + p_ * qty) / (o["qty"] + qty)
            o["qty"] += qty
        else:
            pos[tk] = dict(sleeve=sleeve, qty=qty, entry=p_, entry_date=d, cost_basis=dollars, last=p_)
        return True

    for i in range(start_i, end_i + 1):
        d = cal[i]
        # benchmark: reinvest dividends on ex-date at that close
        if d in spy_div:
            spy_cash += spy_units * spy_div[d]
            spy_units += spy_cash / spy["closes"][i]
            spy_cash = 0.0
        spy_tr = spy_units * spy["closes"][i]
        # mark
        for tk, o in pos.items():
            p_ = price(tk, d)
            if p_ is not None:
                o["last"] = p_
        eq = cash + sum(o["qty"] * o["last"] for o in pos.values())

        # ── sleeve B: ladders (daily) ──
        for tk in list(ladders):
            L = ladders[tk]
            s = px[tk]
            j = s["idx"].get(d)
            if j is None:
                continue
            for k, off in enumerate(p["ladder"]):
                if L["filled"][k] or k == 0:
                    continue
                lim = L["dot_px"] * (1 + off)
                if s["lows"][j] <= lim:
                    fill_px = min(lim, s["closes"][j]) if s["closes"][j] > lim else lim
                    dollars = L["tranche"]
                    if dollars <= cash:
                        qty = dollars / (fill_px * (1 + cost))
                        cash -= dollars
                        o = pos[tk]
                        o["entry"] = (o["entry"] * o["qty"] + fill_px * qty) / (o["qty"] + qty)
                        o["qty"] += qty
                        o["cost_basis"] += dollars
                        L["filled"][k] = True
            if j >= L["exit_i"]:
                close_pos(tk, d, "ladder_hold_done")
                ladders.pop(tk)
        for tk, dpx in dots_by_date.get(d, []):
            if tk in ladders or tk in pos or len(ladders) >= p["b_slots"] or tk not in px:
                continue
            full = eq * p["b_weight"] / p["b_slots"]
            tranche = full / len(p["ladder"])
            if open_pos(tk, d, tranche, "dots", "dot"):
                j = px[tk]["idx"][d]
                ladders[tk] = dict(dot_px=dpx, tranche=tranche, filled=[True] + [False] * (len(p["ladder"]) - 1),
                                   exit_i=j + p["b_hold"])

        # ── regime ──
        if i in me_set:
            me_closes.append(spy["closes"][i])
            n = p["regime_months"]
            if len(me_closes) >= n:
                faber_on = me_closes[-1] > sum(me_closes[-n:]) / n

        # ── sleeve A: trend, on Fridays (or last trading day of the week) ──
        is_rebalance = (i == end_i) or (i + 1 < len(cal) and cal[i + 1].weekday() < d.weekday()) or i == start_i
        if is_rebalance:
            si = spy["idx"][d]
            if p["regime"] == "faber":
                risk_on = faber_on
            elif p["regime"] == "none":
                risk_on = True
            else:
                spy_sma = sma(spy["closes"], p["sma"], si)
                risk_on = spy_sma is not None and spy["closes"][si] > spy_sma
            cands = {}
            for tk in ETF_POOL:
                s = px.get(tk)
                if not s:
                    continue
                j = s["idx"].get(d)
                if j is None:
                    continue
                m = momentum(s["closes"], j, p["mom_long"], p["mom_skip"])
                t_sma = sma(s["closes"], p["sma"], j)
                if m is not None and t_sma is not None and s["closes"][j] > t_sma:
                    cands[tk] = m
            ranked_all = rank_pool(cands)
            defensive = [t for t in rank_pool({t: cands[t] for t in DEFENSIVE_POOL if t in cands}) if cands[t] > 0]
            current = [t for t, o in pos.items() if o["sleeve"] == "trend"]
            if risk_on:
                target = target_holdings(ranked_all, current, p["top_n"])
            elif p["force_liquidate"]:
                target = target_holdings(defensive, current, p["top_n"])
            else:
                # v2: keep what still qualifies on its own merits; new money only defensive
                keep = [t for t in current if t in ranked_all[:2 * p["top_n"]]]
                target = target_holdings(defensive, keep, p["top_n"]) if len(keep) < p["top_n"] else keep[:p["top_n"]]
                target = list(dict.fromkeys(keep + target))[:p["top_n"]]
            for tk in current:
                if tk not in target:
                    close_pos(tk, d, "rotate" if risk_on else "risk_off")
            eq = cash + sum(o["qty"] * o["last"] for o in pos.values())
            slot = eq * p["a_weight"] / p["top_n"]
            for tk in target:
                if tk not in pos:
                    open_pos(tk, d, min(slot, cash), "trend", "momentum")
        equity.append((d, cash + sum(o["qty"] * o["last"] for o in pos.values()), spy_tr))

    # close everything at the end for accounting
    for tk in list(pos):
        close_pos(tk, cal[end_i], "end_of_window")
    eq0, eq1 = capital, equity[-1][1] if equity else capital
    spy0, spy1 = capital, equity[-1][2] if equity else capital
    days = (cal[end_i] - cal[start_i]).days
    stats = dict(final_equity=eq1, cagr=cagr(eq0, eq1, days), max_dd=max_drawdown([e for _, e, _ in equity]),
                 spy_final=spy1, spy_cagr=cagr(spy0, spy1, days), spy_max_dd=max_drawdown([s for _, _, s in equity]),
                 n_trades=len(trades), wins=sum(1 for t in trades if t["pnl"] > 0),
                 days_invested=sum(1 for _, e, _ in equity) )
    stats["beats"] = bool(stats["cagr"] is not None and stats["spy_cagr"] is not None
                          and stats["cagr"] > stats["spy_cagr"] and stats["max_dd"] >= stats["spy_max_dd"])
    return dict(equity=equity, trades=trades, stats=stats)


# ── runner ─────────────────────────────────────────────────────────────

def run_variant(name, overrides=None, window="build") -> dict:
    """Simulate one named variant and persist it. window='build' caps the
    end at BUILD_END; window='sealed' runs SEALED_START..SEALED_END and
    refuses to run twice for the same name (the once-only rule)."""
    from screen.reversal_screen import _conn
    p = dict(DEFAULTS)
    p.update(overrides or {})
    if window == "sealed":
        p["start"], p["end"] = SEALED_START, SEALED_END
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id FROM beat_spy_runs WHERE name=%s AND run_window=%s", (name, window))
            row = c.fetchone()
            if row and window == "sealed":
                log.warning("[beat_spy] sealed run %s already exists (id %s) — refusing to re-run.", name, row[0])
                return {"refused": True, "run_id": row[0]}
        _ensure_history(conn, ETF_POOL)
        dots = _dots(conn, p)
        tickers = set(ETF_POOL) | {tk for tk, _, _ in dots}
        px = _load(conn, tickers)
        spy_div = _spy_dividends(conn)
        res = simulate(px, dots, spy_div, p)
        st = res["stats"]
        with conn.cursor() as c:
            c.execute("DELETE FROM beat_spy_runs WHERE name=%s AND run_window=%s", (name, window))
            c.execute("""INSERT INTO beat_spy_runs (name, run_window, params, start_date, end_date, final_equity, cagr,
                             max_dd, spy_final, spy_cagr, spy_max_dd, n_trades, wins, beats)
                         VALUES (%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                      (name, window, json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in p.items()}, default=str),
                       p["start"], p["end"], st["final_equity"], st["cagr"], st["max_dd"], st["spy_final"],
                       st["spy_cagr"], st["spy_max_dd"], st["n_trades"], st["wins"], st["beats"]))
            run_id = c.fetchone()[0]
            c.executemany("INSERT INTO beat_spy_equity (run_id, d, equity, spy_tr) VALUES (%s,%s,%s,%s)",
                          [(run_id, d, e, s) for d, e, s in res["equity"]])
            c.executemany("""INSERT INTO beat_spy_trades (run_id, sleeve, ticker, entry_date, entry_px, exit_date,
                                 exit_px, qty, pnl, reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          [(run_id, t["sleeve"], t["ticker"], t["entry_date"], t["entry_px"], t["exit_date"],
                            t["exit_px"], t["qty"], t["pnl"], t["reason"]) for t in res["trades"]])
        conn.commit()
        log.info("[beat_spy] %s/%s: eq %.0f cagr %.2f%% dd %.1f%% | SPY cagr %.2f%% dd %.1f%% | beats=%s",
                 name, window, st["final_equity"], 100 * (st["cagr"] or 0), 100 * st["max_dd"],
                 100 * (st["spy_cagr"] or 0), 100 * st["spy_max_dd"], st["beats"])
        st["run_id"] = run_id
        return st
    finally:
        conn.close()


BUILD_VARIANTS = {
    "compass_v1":        {},
    "v1_top3":           dict(top_n=3),
    "v1_top8":           dict(top_n=8),
    "v1_mom63":          dict(mom_long=63, mom_skip=10),
    "v1_mom252":         dict(mom_long=252, mom_skip=21),
    "v1_no_dots":        dict(b_weight=0.0, b_slots=0),
    "v1_a90":            dict(a_weight=0.90, b_weight=0.10),
    "v1_dots_only":      dict(a_weight=0.0, b_weight=0.30, b_slots=15),
    # v2: Faber monthly regime, no forced liquidation, full allocation
    "compass_v2":        dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20),
    "v2_mom252":         dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20, mom_long=252),
    "v2_top3":           dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20, top_n=3),
    "v2_top8":           dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20, top_n=8),
    "v2_trend_only":     dict(regime="faber", force_liquidate=False, a_weight=1.0, b_weight=0.0, b_slots=0),
    "v2_faber_liq":      dict(regime="faber", force_liquidate=True, a_weight=0.80, b_weight=0.20),
    "v2_noregime":       dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20),
}


def run_build_grid() -> bool:
    """Boot: run every build-window variant not yet stored. True when done."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT name FROM beat_spy_runs WHERE run_window='build'")
            have = {r[0] for r in c.fetchall()}
    finally:
        conn.close()
    todo = [n for n in BUILD_VARIANTS if n not in have]
    for n in todo:
        try:
            run_variant(n, BUILD_VARIANTS[n], "build")
        except Exception as e:
            log.exception("[beat_spy] variant %s failed: %s", n, e)
    return not todo
