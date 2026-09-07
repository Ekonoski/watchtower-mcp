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
# v3 (2026-09-07): the CORE pool — broad indexes, the SPDR sectors and the
# old-line industry ETFs. The thematic funds (ARKK, TAN, ICLN, URA, LIT,
# KWEB, FXI, GDXJ, HYG, IHI, IGV, ITA) are excluded ex ante: most did not
# exist before 2010, and a momentum ranking that can reach them buys the
# 2020-21 bubble cohort at the top. Stated as a design choice, not a fit.
ETF_CORE = ("SPY", "QQQ", "IWM", "MDY", "DIA", "RSP", "XLK", "XLF", "XLV", "XLB", "XLE",
            "XLI", "XLP", "XLU", "XLY", "XLRE", "XLC", "XBI", "IBB", "SMH", "SOXX", "XHB",
            "ITB", "XME", "XOP", "OIH", "KRE", "KBE", "XRT", "IYT", "VNQ", "GDX", "TLT", "GLD")
# v4: the INDEX pool — dual momentum among the broad indexes only (the
# Antonacci shape: one winner, or defensive). Sector/industry funds dilute
# the concentration that beat SPY after 2010; the indexes carry it.
ETF_INDEX = ("SPY", "QQQ", "IWM", "MDY", "DIA", "RSP", "TLT", "GLD")
# v5: EQUITY indexes only compete in risk-on; the defensive pool is reached
# only when nothing in the equity pool clears the absolute filter (GEM).
ETF_INDEX_EQ = ("SPY", "QQQ", "IWM", "MDY", "DIA", "RSP")
CALL_DELTA = 0.80          # deep ITM: the option behaves like levered shares with a floor
CALL_TENOR = 270 / 365.0   # ~9 months at entry
CALL_ROLL_DAYS = 60        # roll when fewer than 60 calendar days remain
CALL_SPREAD = 0.02         # 2% of premium per side (modeled prices, per the rules)
CALL_COMMISSION = 0.65     # per contract per side
VOL_PREMIUM = 1.15         # implied ≈ 1.15 × realized (the vol risk premium, stated)
PREMIUM_CAP = 0.25         # total premium at risk ≤ 25% of equity (the rules)


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma):
    """Black-Scholes call, r=0. T in years; T<=0 -> intrinsic."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / sq
    return S * _ncdf(d1) - K * _ncdf(d1 - sq)


def strike_for_delta(S, T, sigma, delta=CALL_DELTA):
    lo, hi = -6.0, 6.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _ncdf(mid) < delta:
            lo = mid
        else:
            hi = mid
    d1 = (lo + hi) / 2
    sq = sigma * math.sqrt(T)
    return S * math.exp(-(d1 * sq - 0.5 * sigma * sigma * T))


def realized_vol(closes, i, n=60):
    """Annualized close-to-close vol of the n returns ending at bar i."""
    lo = max(1, i - n + 1)
    rets = [math.log(closes[k] / closes[k - 1]) for k in range(lo, i + 1) if closes[k - 1] > 0]
    if len(rets) < 15:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 252)
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
                regime="weekly200", regime_months=10, force_liquidate=True,
                # v3: pool ('all' | 'core'), absolute momentum (dual momentum —
                # a name must also be UP over its own lookback), and the option
                # overlay: lev>1 expresses each trend position as deep-ITM calls
                # on lev × the share notional, priced by model (bs_call on
                # realized vol × VOL_PREMIUM), rolled at CALL_ROLL_DAYS, total
                # premium capped at PREMIUM_CAP of equity. lev=1.0 = shares.
                pool="all", abs_mom=False, lev=1.0,
                # v5: rebalance cadence ('weekly' | 'monthly'), and whether each
                # fund must also sit above its own 200-day (sma_filter). GEM as
                # published = monthly, no per-fund SMA, abs_mom only.
                rebalance="weekly", sma_filter=True,
                # v6 (2026-09-07, after the v4 read — 2020: risk-off Mar 13, parked
                # in TLT until Aug 21 while QQQ ran off the low; 2009 the same
                # shape): regime='two_speed' leaves on the 200-day and RE-ENTERS
                # on a close above a RISING fast_sma (the recovery state, whose
                # stop is the fast line until the 200-day is regained). Evaluated
                # daily; a state change forces a rebalance that day. And
                # vol_target (annualized, e.g. 0.15): the trend sleeve's exposure
                # is scaled by vol_target / SPY 60-day realized vol, clamped to
                # [VOL_SCALE_MIN, max_lev]; scale > 1 rides the call overlay.
                fast_sma=50, fast_rise=10, vol_target=None, max_lev=1.0,
                # v6: which defensive assets the book may hold when equities fail
                # ('all' = TLT+GLD; 'bonds' = TLT only — v5 held GLD Mar→Oct 2008
                # for −$19.6k because gold ranked first on momentum into its crash)
                defensive="all",
                # series-hygiene version stamped into every run's params
                defect_guard=2)
VOL_SCALE_MIN = 0.30
RESIZE_BAND = 0.20        # resize a share position only when it drifts 20% from target


def two_speed_step(state, close, slow, fast, fast_prev):
    """Pure. One daily step of the v6 regime machine. States: 'normal'
    (SPY above its slow line), 'off', 'recovery' (re-entered on a rising
    fast line, stopped by it). Any hole in the lines keeps the state."""
    if slow is None or fast is None:
        return state
    if close > slow:
        return "normal"
    if state == "normal":
        return "off"
    rising = fast_prev is not None and fast > fast_prev
    if state == "off":
        return "recovery" if (close > fast and rising) else "off"
    return "recovery" if close > fast else "off"          # state == 'recovery'


def vol_scale(vol_target, spy_vol, max_lev):
    """Pure. Exposure multiplier for the trend sleeve; 1.0 when targeting is off
    or the vol proxy is a hole."""
    if not vol_target or spy_vol is None or spy_vol <= 0:
        return 1.0
    return max(VOL_SCALE_MIN, min(max_lev, vol_target / spy_vol))


DEFECT_JUMP = 3.0          # a close 3x (or 1/3) its prior close is a spliced series, not a move
DEFECT_GAP_DAYS = 10       # more than 10 calendar days between stored bars is a hole


def series_defects(dates, closes, max_jump=DEFECT_JUMP, max_gap_days=DEFECT_GAP_DAYS):
    """Pure. Dates at which a stored series stops being one continuous tape:
    a close beyond max_jump× its prior close (ticker reuse — 'AI' was
    Arlington Asset at $2.83 until C3.ai took the symbol at $100+) or a
    gap longer than max_gap_days (TFIN's 2008 bars followed by 2022's).
    The defect is stamped on the FIRST bar after the break."""
    out = []
    for i in range(1, len(dates)):
        c0, c1 = closes[i - 1], closes[i]
        if (dates[i] - dates[i - 1]).days > max_gap_days:
            out.append((dates[i], "gap"))
        elif c0 > 0 and (c1 / c0 > max_jump or c1 / c0 < 1.0 / max_jump):
            out.append((dates[i], "splice"))
    return out


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


# Leveraged / inverse ETPs are daily-reset decay products, not companies in
# drawdown — the green-dot claim was about stocks. 2026-09-07 census: ~30
# of them (JNUG, NUGT, SCO, SQQQ, SPXS, TSLQ…) sat in the deep-dot universe.
LEVERAGED_ETP_RE = (r"(direxion daily|microsectors|proshares - ultra|proshares - short|tradr |velocityshares"
                    r"|leveraged etn| [23]x |[23]x (bull|bear|leveraged|inverse|long|short)|(bull|bear) [23]x)")


def is_leveraged_etp(company_name):
    """Pure. True when a security name reads as a leveraged/inverse ETP."""
    import re
    return bool(company_name) and re.search(LEVERAGED_ETP_RE, company_name, re.I) is not None


def _dots(conn, p):
    """Deep dots on names liquid today (>= $10M/day trailing 90d), leveraged
    and inverse ETPs excluded by name."""
    with conn.cursor() as c:
        c.execute("""WITH liq AS (SELECT ticker FROM daily_prices WHERE trade_date >= CURRENT_DATE - 90
                                  GROUP BY ticker HAVING avg(close*volume) >= 10e6)
                     SELECT g.ticker, g.dot_date, g.px_at_dot FROM greendot_dots g JOIN liq USING (ticker)
                     LEFT JOIN tickers t ON t.ticker = g.ticker
                     WHERE g.cross_depth <= %s AND g.drawdown_pct >= %s AND g.px_at_dot >= 2
                       AND NOT COALESCE(t.company_name ~* %s, false)
                     ORDER BY g.dot_date""", (p["b_depth"], p["b_dd"], LEVERAGED_ETP_RE))
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
    ts_state = "normal"       # v6 two-speed regime state
    ts_scale = 1.0            # v6 vol-target multiplier, refreshed at rebalance
    # series hygiene (2026-09-07, the 'AI' $2.83→$134 splice and TFIN's 2008→2023
    # hole): defects per ticker, stamped on the first bar after the break. A held
    # ladder exits at the LAST REAL PRINT when its tape breaks; a dot on a series
    # broken inside the prior two years is refused. No lookahead either way.
    defects = {tk: series_defects(s["dates"], s["closes"]) for tk, s in px.items()}
    defect_at = {tk: {d_: kind for d_, kind in v} for tk, v in defects.items()}
    n_defect_exits = n_dots_refused = 0

    def price(tk, d):
        s = px.get(tk)
        if not s:
            return None
        i = s["idx"].get(d)
        return s["closes"][i] if i is not None else None

    def call_mark(o, d):
        """Model value of a call position at date d (per share × qty)."""
        s = px[o["ticker"]]
        j = s["idx"].get(d)
        if j is None:
            return o["last_val"]
        S = s["closes"][j]
        T = max(0.0, (o["expiry"] - d).days / 365.0)
        sig = realized_vol(s["closes"], j) or o["sigma"]
        return bs_call(S, o["strike"], T, sig * VOL_PREMIUM) * o["qty"]

    def value(o, d):
        if o["kind"] == "call":
            return call_mark(o, d)
        p_ = price(o["ticker"], d)
        return (p_ if p_ is not None else o["last"]) * o["qty"]

    def close_pos(tk, d, reason, px_override=None):
        nonlocal cash
        o = pos.pop(tk)
        if o["kind"] == "call":
            gross = call_mark(o, d)
            proceeds = gross * (1 - CALL_SPREAD) - CALL_COMMISSION * (o["qty"] / 100.0)
            p_ = price(tk, d) or o["last"]
        else:
            p_ = px_override if px_override is not None else price(tk, d)
            if p_ is None:
                p_ = o["last"]
            proceeds = o["qty"] * p_ * (1 - cost)
        cash += proceeds
        trades.append(dict(sleeve=o["sleeve"], ticker=tk, entry_date=o["entry_date"], entry_px=o["entry"],
                           exit_date=d, exit_px=p_, qty=o["qty"], pnl=proceeds - o["cost_basis"],
                           reason=reason + ("" if o["kind"] == "shares" else "_call")))

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
            pos[tk] = dict(kind="shares", ticker=tk, sleeve=sleeve, qty=qty, entry=p_, entry_date=d,
                           cost_basis=dollars, last=p_)
        return True

    def trim_pos(tk, d, dollars):
        """Sell `dollars` of a share position (v6 resize). Records the sold
        slice as its own trade row so the P&L stays auditable."""
        nonlocal cash
        o = pos[tk]
        p_ = price(tk, d)
        if p_ is None or o["kind"] != "shares" or dollars <= 0:
            return
        qty = min(o["qty"], dollars / p_)
        proceeds = qty * p_ * (1 - cost)
        basis = o["cost_basis"] * (qty / o["qty"])
        o["qty"] -= qty
        o["cost_basis"] -= basis
        cash += proceeds
        trades.append(dict(sleeve=o["sleeve"], ticker=tk, entry_date=o["entry_date"], entry_px=o["entry"],
                           exit_date=d, exit_px=p_, qty=qty, pnl=proceeds - basis, reason="resize"))
        if o["qty"] <= 1e-9:
            pos.pop(tk)

    def open_call(tk, d, notional, sleeve, eq_now, fallback=None):
        """Deep-ITM call on `notional` dollars of exposure. Premium is the
        cash outlay; refused (falls back to `fallback` dollars of shares —
        default notional / lev) when it would breach the premium cap or the
        vol proxy is a hole."""
        nonlocal cash
        fb = notional / p["lev"] if fallback is None else fallback
        s = px.get(tk)
        j = s["idx"].get(d) if s else None
        if j is None:
            return False
        S = s["closes"][j]
        sig = realized_vol(s["closes"], j)
        if sig is None:
            return open_pos(tk, d, min(fb, cash), sleeve, "momentum")
        sig_i = max(0.10, sig * VOL_PREMIUM)
        K = strike_for_delta(S, CALL_TENOR, sig_i)
        prem_ps = bs_call(S, K, CALL_TENOR, sig_i)
        qty = notional / S
        outlay = prem_ps * qty * (1 + CALL_SPREAD) + CALL_COMMISSION * (qty / 100.0)
        at_risk = sum(o["cost_basis"] for o in pos.values() if o["kind"] == "call")
        if outlay > cash or at_risk + outlay > PREMIUM_CAP * eq_now:
            return open_pos(tk, d, min(fb, cash), sleeve, "momentum")
        cash -= outlay
        pos[tk] = dict(kind="call", ticker=tk, sleeve=sleeve, qty=qty, entry=S, entry_date=d,
                       cost_basis=outlay, last=S, last_val=prem_ps * qty, strike=K,
                       expiry=d + dt.timedelta(days=int(CALL_TENOR * 365)), sigma=sig)
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
            if o["kind"] == "call":
                o["last_val"] = call_mark(o, d)
        eq = cash + sum(value(o, d) for o in pos.values())
        # roll calls approaching expiry (same name, fresh tenor)
        for tk in [t for t, o in pos.items() if o["kind"] == "call" and (o["expiry"] - d).days < CALL_ROLL_DAYS]:
            o = pos[tk]
            notional = o["qty"] * (price(tk, d) or o["last"])
            close_pos(tk, d, "roll")
            open_call(tk, d, notional, o["sleeve"], eq)

        # ── sleeve B: ladders (daily) ──
        for tk in list(ladders):
            L = ladders[tk]
            s = px[tk]
            j = s["idx"].get(d)
            if j is None:
                continue
            if d in defect_at[tk]:
                # the tape broke here: sell at the last real print, never at the spliced one
                close_pos(tk, d, "series_" + defect_at[tk][d], px_override=s["closes"][j - 1] if j > 0 else None)
                ladders.pop(tk)
                n_defect_exits += 1
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
            if any(d - dt.timedelta(days=730) < dd_ <= d for dd_, _ in defects[tk]):
                n_dots_refused += 1              # the dot was computed on a broken tape
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

        # v6 two-speed regime: stepped daily on SPY; a state change forces a rebalance
        forced = False
        if p["regime"] == "two_speed":
            prev_state = ts_state
            ts_state = two_speed_step(ts_state, spy["closes"][i], sma(spy["closes"], p["sma"], i),
                                      sma(spy["closes"], p["fast_sma"], i),
                                      sma(spy["closes"], p["fast_sma"], i - p["fast_rise"]) if i >= p["fast_rise"] else None)
            forced = (ts_state != prev_state) and ((ts_state == "off") != (prev_state == "off"))

        # ── sleeve A: trend, on Fridays (or last trading day of the week) ──
        if p["rebalance"] == "monthly":
            is_rebalance = (i in me_set) or i == start_i or i == end_i
        else:
            is_rebalance = (i == end_i) or (i + 1 < len(cal) and cal[i + 1].weekday() < d.weekday()) or i == start_i
        if is_rebalance or forced:
            si = spy["idx"][d]
            if p["regime"] == "faber":
                risk_on = faber_on
            elif p["regime"] == "none":
                risk_on = True
            elif p["regime"] == "two_speed":
                risk_on = ts_state != "off"
            else:
                spy_sma = sma(spy["closes"], p["sma"], si)
                risk_on = spy_sma is not None and spy["closes"][si] > spy_sma
            ts_scale = vol_scale(p["vol_target"], realized_vol(spy["closes"], si), p["max_lev"])
            pool = {"core": ETF_CORE, "index": ETF_INDEX, "index_eq": ETF_INDEX_EQ}.get(p["pool"], ETF_POOL)
            defpool = DEFENSIVE_POOL if p.get("defensive", "all") == "all" else ("TLT",)
            cands = {}
            for tk in set(pool) | set(defpool):
                s = px.get(tk)
                if not s:
                    continue
                j = s["idx"].get(d)
                if j is None:
                    continue
                m = momentum(s["closes"], j, p["mom_long"], p["mom_skip"])
                if m is None:
                    continue
                if p["sma_filter"]:
                    t_sma = sma(s["closes"], p["sma"], j)
                    if t_sma is None or s["closes"][j] <= t_sma:
                        continue
                if p["abs_mom"] and m <= 0:
                    continue
                cands[tk] = m
            ranked_all = [t for t in rank_pool(cands) if t in pool]
            defensive = [t for t in rank_pool({t: cands[t] for t in defpool if t in cands}) if cands[t] > 0]
            if risk_on and not ranked_all:
                ranked_all = defensive          # GEM: equities fail the absolute filter -> bonds/gold
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
            eq = cash + sum(value(o, d) for o in pos.values())
            slot = eq * p["a_weight"] / p["top_n"]
            exposure = slot * ts_scale                      # v6: vol-targeted exposure per slot
            use_calls = risk_on and (p["lev"] > 1.0 or ts_scale > 1.0)
            for tk in target:
                if tk not in pos:
                    if use_calls and tk not in defpool:
                        open_call(tk, d, exposure * max(p["lev"], 1.0), "trend", eq, fallback=min(exposure, slot))
                    else:
                        open_pos(tk, d, min(exposure, cash), "trend", "momentum")
                elif p["vol_target"] and pos[tk]["kind"] == "shares" and pos[tk]["sleeve"] == "trend":
                    # resize held shares toward the new exposure (calls re-size at roll)
                    held = value(pos[tk], d)
                    want = min(exposure, slot) if not use_calls or tk in defpool else exposure
                    if held > want * (1 + RESIZE_BAND):
                        trim_pos(tk, d, held - want)
                    elif held < want * (1 - RESIZE_BAND):
                        open_pos(tk, d, min(want - held, cash), "trend", "resize")
        equity.append((d, cash + sum(value(o, d) for o in pos.values()), spy_tr))

    # close everything at the end for accounting
    for tk in list(pos):
        close_pos(tk, cal[end_i], "end_of_window")
    eq0, eq1 = capital, equity[-1][1] if equity else capital
    spy0, spy1 = capital, equity[-1][2] if equity else capital
    days = (cal[end_i] - cal[start_i]).days
    stats = dict(final_equity=eq1, cagr=cagr(eq0, eq1, days), max_dd=max_drawdown([e for _, e, _ in equity]),
                 spy_final=spy1, spy_cagr=cagr(spy0, spy1, days), spy_max_dd=max_drawdown([s for _, _, s in equity]),
                 n_trades=len(trades), wins=sum(1 for t in trades if t["pnl"] > 0),
                 days_invested=sum(1 for _, e, _ in equity),
                 defect_exits=n_defect_exits, dots_refused_defect=n_dots_refused)
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
        log.info("[beat_spy] %s/%s: eq %.0f cagr %.2f%% dd %.1f%% | SPY cagr %.2f%% dd %.1f%% | beats=%s "
                 "| series defects: %d ladder exits, %d dots refused",
                 name, window, st["final_equity"], 100 * (st["cagr"] or 0), 100 * st["max_dd"],
                 100 * (st["spy_cagr"] or 0), 100 * st["spy_max_dd"], st["beats"],
                 st["defect_exits"], st["dots_refused_defect"])
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
    # v3: core pool, dual momentum (abs_mom), 252-21 lookback; then the call overlay
    "compass_v3":        dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="core", abs_mom=True),
    "v3_top3":           dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="core", abs_mom=True, top_n=3),
    "v3_top8":           dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="core", abs_mom=True, top_n=8),
    "v3_no_dots":        dict(regime="faber", force_liquidate=False, a_weight=1.0, b_weight=0.0, b_slots=0,
                              mom_long=252, pool="core", abs_mom=True),
    "v3_calls_1p5":      dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="core", abs_mom=True, lev=1.5),
    "v3_calls_2p0":      dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="core", abs_mom=True, lev=2.0),
    "v3_top3_calls_1p5": dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="core", abs_mom=True, top_n=3, lev=1.5),
    # v4: index-only dual momentum (concentration), and a heavier washout sleeve
    "v4_index_top1":     dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="index", abs_mom=True, top_n=1),
    "v4_index_top2":     dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="index", abs_mom=True, top_n=2),
    "v4_index_top1_nodots": dict(regime="faber", force_liquidate=False, a_weight=1.0, b_weight=0.0, b_slots=0,
                                 mom_long=252, pool="index", abs_mom=True, top_n=1),
    "v4_index_top1_dots40": dict(regime="faber", force_liquidate=False, a_weight=0.60, b_weight=0.40, b_slots=20,
                                 mom_long=252, pool="index", abs_mom=True, top_n=1),
    "v4_dots_heavy":     dict(regime="faber", force_liquidate=False, a_weight=0.50, b_weight=0.50, b_slots=25,
                              mom_long=252, pool="index", abs_mom=True, top_n=1),
    "v4_index_top1_calls_1p5": dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                                    mom_long=252, pool="index", abs_mom=True, top_n=1, lev=1.5),
    "v4_index_mom126":   dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=126, pool="index", abs_mom=True, top_n=1),
    # v5: GEM as published — monthly, equity indexes only in risk-on, abs_mom the only filter
    "v5_gem":            dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False),
    "v5_gem_nodots":     dict(regime="none", force_liquidate=False, a_weight=1.0, b_weight=0.0, b_slots=0,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False),
    "v5_gem_skip21":     dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False),
    "v5_gem_top2":       dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=2,
                              rebalance="monthly", sma_filter=False),
    "v5_gem_faber":      dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False),
    "v5_gem_calls_1p5":  dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, lev=1.5),
    "v5_gem_dots40":     dict(regime="none", force_liquidate=False, a_weight=0.60, b_weight=0.40, b_slots=20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False),
    "v5_core_monthly":   dict(regime="faber", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, pool="core", abs_mom=True, top_n=5,
                              rebalance="monthly", sma_filter=True),
    # v6: the two-speed regime (fast re-entry after a crash) and vol targeting
    "v6_ts_monthly":     dict(regime="two_speed", force_liquidate=True, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False),
    "v6_ts_weekly":      dict(regime="two_speed", force_liquidate=True, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="weekly", sma_filter=False),
    "v6_ts_mom126_weekly": dict(regime="two_speed", force_liquidate=True, a_weight=0.80, b_weight=0.20,
                                mom_long=126, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                                rebalance="weekly", sma_filter=False),
    "v6_vt15":           dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, vol_target=0.15, max_lev=1.0),
    "v6_vt15_lev1p5":    dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, vol_target=0.15, max_lev=1.5),
    "v6_vt20_lev2":      dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, vol_target=0.20, max_lev=2.0),
    "v6_ts_vt15_lev1p5": dict(regime="two_speed", force_liquidate=True, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, vol_target=0.15, max_lev=1.5),
    "v6_ts_vt15_lev1p5_weekly": dict(regime="two_speed", force_liquidate=True, a_weight=0.80, b_weight=0.20,
                                     mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                                     rebalance="weekly", sma_filter=False, vol_target=0.15, max_lev=1.5),
    "v6_ts_vt15_lev1p5_nodots": dict(regime="two_speed", force_liquidate=True, a_weight=1.0, b_weight=0.0, b_slots=0,
                                     mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                                     rebalance="monthly", sma_filter=False, vol_target=0.15, max_lev=1.5),
    # v6b: bonds-only defensive (v5 held GLD through its 2008 crash)
    "v6_ts_bonds":       dict(regime="two_speed", force_liquidate=True, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v6_gem_bonds":      dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v6_ts_vt15_lev1p5_bonds": dict(regime="two_speed", force_liquidate=True, a_weight=0.80, b_weight=0.20,
                                    mom_long=252, mom_skip=0, pool="index_eq", abs_mom=True, top_n=1,
                                    rebalance="monthly", sma_filter=False, vol_target=0.15, max_lev=1.5,
                                    defensive="bonds"),
    # v7: the clean grid's only survivor was v5_gem_skip21 (9.76% vs 9.61%) and
    # bonds-only defensive added +1.2 pts to v5_gem. Combine them, and walk the
    # skip / lookback neighbors — a beat that lives in one cell is a curve fit.
    "v7_skip21_bonds":   dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_skip10":         dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=10, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_skip15":         dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=15, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_skip30":         dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=30, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_skip42":         dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=252, mom_skip=42, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_mom189_skip21":  dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=189, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_mom315_skip21":  dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                              mom_long=315, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                              rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_skip21_bonds_top2": dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                                 mom_long=252, mom_skip=21, pool="index_eq", abs_mom=True, top_n=2,
                                 rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_skip21_bonds_nodots": dict(regime="none", force_liquidate=False, a_weight=1.0, b_weight=0.0, b_slots=0,
                                   mom_long=252, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                                   rebalance="monthly", sma_filter=False, defensive="bonds"),
    "v7_skip21_bonds_vt20_lev2": dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                                      mom_long=252, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                                      rebalance="monthly", sma_filter=False, defensive="bonds",
                                      vol_target=0.20, max_lev=2.0),
    "v7_skip21_bonds_calls_1p5": dict(regime="none", force_liquidate=False, a_weight=0.80, b_weight=0.20,
                                      mom_long=252, mom_skip=21, pool="index_eq", abs_mom=True, top_n=1,
                                      rebalance="monthly", sma_filter=False, defensive="bonds", lev=1.5),
}


DEFECT_GUARD_VERSION = 2   # bump when series hygiene changes; older rows re-run
# v1: splice/gap guard on the dots sleeve. v2: leveraged/inverse ETPs excluded
# from the dot universe by name.


def run_build_grid() -> bool:
    """Boot: run every build-window variant not yet stored under the current
    series-hygiene version. Rows written before the guard (2026-09-07: TFIN's
    phantom $52k sat in 28 of them) are kept for the record as
    <name>_pre_guard (v0) / <name>_guardN (later versions) and the canonical
    name re-runs. True when done."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""UPDATE beat_spy_runs
                         SET name = name || CASE WHEN COALESCE((params->>'defect_guard')::int, 0) = 0
                                                 THEN '_pre_guard'
                                                 ELSE '_guard' || (params->>'defect_guard') END
                         WHERE run_window='build' AND name NOT LIKE '%%guard%%'
                           AND COALESCE((params->>'defect_guard')::int, 0) < %s""", (DEFECT_GUARD_VERSION,))
            if c.rowcount:
                log.warning("[beat_spy] %d build runs predate series-hygiene v%d — kept as *_pre_guard, re-running.",
                            c.rowcount, DEFECT_GUARD_VERSION)
            conn.commit()
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
