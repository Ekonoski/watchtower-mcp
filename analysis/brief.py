"""
Watchtower Brief — the one-call 360-degree read on a ticker.

Assembles every feed the system already maintains into one structured
bundle: price structure and levels, our signals (patterns graded by
their own backtest, oscillator, momentum), dealer gamma with the
magnitude rule applied, rotation context, fundamentals, social,
insiders, IV, and recent alert history. Database + cached reads plus
the two live helpers the dashboard drawer already uses (levels engine,
intraday snapshot) — one call, ~5s, same shape every time.

House style is baked into the formatter: freshness stamps per section,
force before levels (net-GEX magnitude gates how much the walls are
worth), sample sizes on every backtest stat, and a closing read
expressed as levels, not predictions.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)


def _conn():
    from screen.reversal_screen import _conn as c
    return c()


def _rows(sql: str, params: tuple) -> list:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _f(v):
    return float(v) if v is not None else None


# ---------------------------------------------------------------- sections

def _price_block(ticker: str) -> dict:
    rows = _rows("""
        SELECT trade_date, close, high, low, volume FROM daily_prices
        WHERE ticker = %s ORDER BY trade_date DESC LIMIT 260
    """, (ticker,))
    if not rows:
        return {}
    closes = [_f(r[1]) for r in rows]          # newest first
    last, as_of = closes[0], rows[0][0]

    def ret(n):
        return round((last / closes[n] - 1) * 100, 2) if len(closes) > n and closes[n] else None

    def sma(n):
        return round(sum(closes[:n]) / n, 2) if len(closes) >= n else None

    highs = [_f(r[2]) for r in rows]
    lows = [_f(r[3]) for r in rows]
    vols = [_f(r[4]) or 0 for r in rows]
    hi52, lo52 = max(highs), min(lows)
    return {
        "as_of": str(as_of), "close": last, "bars": len(rows),
        "ret_1d": ret(1), "ret_1w": ret(5), "ret_1m": ret(21),
        "ret_3m": ret(63), "ret_6m": ret(126),
        "sma20": sma(20), "sma50": sma(50), "sma200": sma(200),
        "hi_52w": hi52, "lo_52w": lo52,
        "off_high_pct": round((last / hi52 - 1) * 100, 1) if hi52 else None,
        "off_low_pct": round((last / lo52 - 1) * 100, 1) if lo52 else None,
        "vol_vs_20d": round(vols[0] / (sum(vols[1:21]) / 20), 2)
                      if len(vols) > 21 and sum(vols[1:21]) else None,
    }


def _patterns_block(ticker: str) -> dict:
    pats = _rows("""
        SELECT timeframe, pattern, direction, status, trigger_price, target,
               invalid_level, last_close, dist_to_trigger_pct, score,
               detected_at::date
        FROM pattern_scan WHERE ticker = %s ORDER BY score DESC
    """, (ticker,))
    stats = {}
    if pats:
        # Pattern-level grades from our own replay — market-wide, current
        # engine + measurement version only (the truncate guarantees that).
        for p, n, hit, w1, stop_r in _rows("""
            SELECT pattern, count(*),
                   round(avg(CASE WHEN outcome='target' THEN 1.0
                                  WHEN outcome='invalid' THEN 0.0 END) * 100, 1),
                   round(avg((win_1r)::int) * 100, 1),
                   round(avg(realized_r) FILTER (WHERE outcome='invalid')::numeric, 2)
            FROM pattern_backtest GROUP BY pattern
        """, ()):
            stats[p] = {"n": n, "hit_pct": _f(hit), "win1r_pct": _f(w1),
                        "avg_stop_r": _f(stop_r)}
    return {
        "rows": [{"timeframe": r[0], "pattern": r[1], "direction": r[2],
                  "status": r[3], "trigger": _f(r[4]), "target": _f(r[5]),
                  "invalid": _f(r[6]), "last_close": _f(r[7]),
                  "dist_to_trigger_pct": _f(r[8]), "score": _f(r[9]),
                  "detected": str(r[10])} for r in pats],
        "stats": stats,
    }


def _oscillator_block(ticker: str) -> list:
    return [{"timeframe": r[0], "direction": r[1], "confluence": _f(r[2]),
             "rsi": _f(r[3]), "signals": list((r[4] or {}).keys()),
             "bar_ts": str(r[5])}
            for r in _rows("""
                SELECT timeframe, direction, confluence_score, rsi, signals,
                       bar_ts
                FROM oscillator_scan WHERE ticker = %s ORDER BY timeframe
            """, (ticker,))]


def _gamma_block(ticker: str) -> dict:
    rows = _rows("""
        SELECT as_of, spot, call_wall, put_wall, gamma_flip, net_gex, regime,
               computed_at
        FROM gex_levels WHERE ticker = %s ORDER BY as_of DESC LIMIT 1
    """, (ticker,))
    if not rows:
        return {}
    r = rows[0]
    net = _f(r[5])
    mag = None
    if net is not None:
        a = abs(net)
        mag = ("load-bearing" if a >= 1.0 else
               "moderate" if a >= 0.05 else "decoration")
    return {"as_of": str(r[0]), "spot": _f(r[1]), "call_wall": _f(r[2]),
            "put_wall": _f(r[3]), "gamma_flip": _f(r[4]), "net_gex_bn": net,
            "regime": r[6], "magnitude": mag, "computed_at": str(r[7])}


def _rotation_block(ticker: str) -> dict:
    out = {}
    rows = _rows("""
        SELECT company_name, sector, industry, market_cap, rs_pct, rev_yoy,
               gross_margin, piotroski_score, altman_z_score, price
        FROM screener_snapshot WHERE ticker = %s
    """, (ticker,))
    if rows:
        r = rows[0]
        out = {"company_name": r[0], "sector": r[1], "industry": r[2],
               "market_cap": _f(r[3]), "rs_pct": _f(r[4]),
               "rev_yoy": _f(r[5]), "gross_margin": _f(r[6]),
               "piotroski": _f(r[7]), "altman_z": _f(r[8])}
        if r[1]:
            out["sector_rank"] = {
                tf: {"rank": int(rk), "median_ret_pct": _f(ret)}
                for tf, rk, ret in _rows("""
                    SELECT tf, rank, round(median_ret * 100, 1)
                    FROM sector_heat_snapshot
                    WHERE sector = %s AND weight = 'median'
                      AND tf IN ('weekly', 'monthly')
                """, (r[1],))}
    return out


def _iv_block(ticker: str) -> dict:
    rows = _rows("""
        SELECT as_of, atm_iv, call_oi, put_oi, skew FROM iv_history
        WHERE ticker = %s ORDER BY as_of DESC LIMIT 120
    """, (ticker,))
    if not rows or rows[0][1] is None:
        return {}
    ivs = sorted(_f(r[1]) for r in rows if r[1] is not None)
    cur = _f(rows[0][1])
    pct = round(100 * sum(1 for v in ivs if v <= cur) / len(ivs)) if ivs else None
    c_oi, p_oi = rows[0][2] or 0, rows[0][3] or 0
    out = {"as_of": str(rows[0][0]), "atm_iv": cur, "iv_percentile": pct,
           "window_days": len(rows),
           "put_call_oi": round(p_oi / c_oi, 2) if c_oi else None}
    skews = [_f(r[4]) for r in rows if r[4] is not None]
    if skews:
        out["skew"] = skews[0]
        out["skew_pctile"] = round(100 * sum(1 for s in sorted(skews)
                                             if s <= skews[0]) / len(skews))
    return out


def _insider_block(ticker: str) -> list:
    return [{"quarter": f"{int(r[0])}Q{int(r[1])}",
             "open_market_buys": r[2], "open_market_sells": r[3],
             "acquired": _f(r[4]), "disposed": _f(r[5])}
            for r in _rows("""
                SELECT fiscal_year, fiscal_quarter, total_purchases,
                       total_sales, total_acquired, total_disposed
                FROM insider_stats WHERE ticker = %s
                ORDER BY fiscal_year DESC, fiscal_quarter DESC LIMIT 4
            """, (ticker,))]


def _alerts_block(ticker: str) -> list:
    return [{"date": str(r[0]), "type": r[1], "signal": r[2],
             "entry": _f(r[3]), "score": _f(r[4])}
            for r in _rows("""
                SELECT alert_date, alert_type, signal_type, entry_price, score
                FROM alert_log WHERE ticker = %s
                ORDER BY alert_date DESC LIMIT 8
            """, (ticker,))]


def _social_block(ticker: str) -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_jsonb(b) FROM social_buzz b "
                        "WHERE ticker = %s LIMIT 1", (ticker,))
            r = cur.fetchone()
            return dict(r[0]) if r else {}
    finally:
        conn.close()


def _momentum_block(ticker: str) -> dict:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_jsonb(m) FROM momentum_scan m "
                        "WHERE ticker = %s LIMIT 1", (ticker,))
            r = cur.fetchone()
            return dict(r[0]) if r else {}
    finally:
        conn.close()


def _fundamentals_block(ticker: str) -> dict:
    """Latest reported quarter + trend context from fundamentals_quarterly.
    Headline row = newest quarter that actually has revenue (FMP sometimes
    files a mostly-null shell for the latest period)."""
    rows = _rows("""
        SELECT fiscal_year, fiscal_quarter, period_end_date, report_date,
               revenue, gross_margin, operating_margin, net_income,
               free_cash_flow, operating_cash_flow, ebitda, total_debt,
               cash_and_equivalents, total_equity, roe
        FROM fundamentals_quarterly WHERE ticker = %s
        ORDER BY period_end_date DESC NULLS LAST LIMIT 8
    """, (ticker,))
    if not rows:
        return {}
    cur = next((r for r in rows if r[4] is not None), rows[0])
    yoy = next((r for r in rows
                if r[0] == cur[0] - 1 and r[1] == cur[1]
                and r[4] is not None), None)
    debt, cash = _f(cur[11]), _f(cur[12])
    equity = _f(cur[13])
    net_debt = (debt - cash) if debt is not None and cash is not None else None
    old_debt = next((_f(r[11]) - _f(r[12]) for r in reversed(rows)
                     if r[11] is not None and r[12] is not None), None)
    rev, rev_y = _f(cur[4]), _f(yoy[4]) if yoy else None
    return {
        "quarter": f"Q{int(cur[1])} FY{int(cur[0])}",
        "period_end": str(cur[2]) if cur[2] else None,
        "filed": str(cur[3]) if cur[3] else None,
        "revenue": rev,
        "rev_yoy_pct": round((rev / rev_y - 1) * 100, 1) if rev and rev_y else None,
        "gross_margin_pct": round(_f(cur[5]) * 100, 1) if cur[5] is not None else None,
        "op_margin_pct": round(_f(cur[6]) * 100, 1) if cur[6] is not None else None,
        "net_income": _f(cur[7]),
        "fcf": _f(cur[8]),
        "ocf": _f(cur[9]),
        "ebitda": _f(cur[10]),
        "net_debt": net_debt,
        "net_debt_oldest": old_debt,
        "debt_to_equity": round(debt / equity, 2) if debt is not None and equity else None,
        "roe_pct": round(_f(cur[14]) * 100, 1) if cur[14] is not None else None,
        "quarters_on_file": len(rows),
    }


def _short_block(ticker: str) -> dict:
    try:
        from analysis.short_side import get_short_context
        return get_short_context(ticker)
    except Exception:
        return {}


def _vol_regime_block() -> dict:
    """Market-wide vol dial — same for every ticker, cheap DB read."""
    try:
        from analysis.vix import get_vix_context
        return get_vix_context()
    except Exception:
        return {}


def build_brief(ticker: str) -> dict:
    """Fan the independent reads out on a small pool — same trick as the
    dashboard drawer. Levels and intraday are the only network calls."""
    ticker = ticker.upper().strip()
    out = {"ticker": ticker}

    price = None
    try:
        from screen.intraday_screen import run_screen as run_intraday
        rows = run_intraday(min_score=0.0, single_ticker=ticker)
        if rows and not rows[0].get("error"):
            out["intraday"] = rows[0]
            price = rows[0].get("current_price")
    except Exception as e:
        out["intraday_error"] = str(e)[:100]

    def _levels():
        from analysis.levels import compute_levels
        return compute_levels(ticker, current_price=price)

    def _fundamentals():
        out = _fundamentals_block(ticker)
        try:
            from analysis.fundamental_value import compute_fair_value
            out["fair_value"] = compute_fair_value(ticker, price_override=price)
        except Exception as e:
            out["fair_value_error"] = str(e)[:80]
        return out

    tasks = {
        "price": lambda: _price_block(ticker),
        "levels": _levels,
        "patterns": lambda: _patterns_block(ticker),
        "oscillator": lambda: _oscillator_block(ticker),
        "momentum": lambda: _momentum_block(ticker),
        "gamma": lambda: _gamma_block(ticker),
        "rotation": lambda: _rotation_block(ticker),
        "fundamentals": _fundamentals,
        "social": lambda: _social_block(ticker),
        "insider": lambda: _insider_block(ticker),
        "iv": lambda: _iv_block(ticker),
        "alerts": lambda: _alerts_block(ticker),
        "vol_regime": _vol_regime_block,
        "short_side": lambda: _short_block(ticker),
    }
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(fn): name for name, fn in tasks.items()}
        for fut, name in futs.items():
            try:
                out[name] = fut.result(timeout=45)
            except Exception as e:
                out[f"{name}_error"] = str(e)[:100]
    return out


# ---------------------------------------------------------------- formatter

def _money(v):
    if v is None:
        return "—"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"${v / div:,.1f}{suf}"
    return f"${v:,.0f}"


def _pctf(v, signed=True):
    if v is None:
        return "—"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def format_brief(d: dict) -> str:
    t = d.get("ticker", "?")
    rot = d.get("rotation") or {}
    name = rot.get("company_name") or ""
    L = [f"## Watchtower Brief — ${t}" + (f" ({name})" if name else ""), ""]

    # -- structure & trend
    p = d.get("price") or {}
    intr = d.get("intraday") or {}
    live = intr.get("current_price")
    if p:
        L.append(f"### Structure & Trend  *(bars through {p.get('as_of')}"
                 + (", live quote delayed ~15m" if live else "") + ")*")
        px = live or p.get("close")
        L.append(f"- Price ${px:,.2f}" +
                 (f" ({_pctf(intr.get('change_pct'))} today)" if intr.get("change_pct") is not None else "") +
                 f" · 1w {_pctf(p.get('ret_1w'))} · 1m {_pctf(p.get('ret_1m'))}"
                 f" · 3m {_pctf(p.get('ret_3m'))} · 6m {_pctf(p.get('ret_6m'))}")
        smas = []
        for k, lbl in (("sma20", "20d"), ("sma50", "50d"), ("sma200", "200d")):
            if p.get(k) and px:
                smas.append(f"{'above' if px >= p[k] else 'below'} {lbl} (${p[k]:,.2f})")
        if smas:
            L.append("- " + " · ".join(smas))
        L.append(f"- 52w range ${p.get('lo_52w'):,.2f}–${p.get('hi_52w'):,.2f}"
                 f" ({_pctf(p.get('off_high_pct'))} off high, {_pctf(p.get('off_low_pct'))} off low)"
                 + (f" · volume {p['vol_vs_20d']:.1f}x 20d avg" if p.get("vol_vs_20d") else ""))
        L.append("")

    # -- levels ladder (levels engine + gamma walls merged around price)
    lv = d.get("levels") or {}
    gx = d.get("gamma") or {}
    px = live or p.get("close")
    if px and (lv.get("support") or lv.get("resistance") or gx):
        ladder = []
        for s in (lv.get("support") or [])[:4]:
            ladder.append((s["price"], f"support {'★' * int(s.get('stars', 0))}"))
        for r in (lv.get("resistance") or [])[:4]:
            ladder.append((r["price"], f"resistance {'★' * int(r.get('stars', 0))}"))
        for key, lbl in (("put_wall", "put wall"), ("gamma_flip", "gamma flip"),
                         ("call_wall", "call wall")):
            if gx.get(key):
                ladder.append((gx[key], f"{lbl} (options)"))
        ladder.sort(key=lambda x: -x[0])
        L.append("### Level Ladder  *(chart levels ★=multi-timeframe touches; options levels from "
                 + (f"{gx.get('as_of')} chain)*" if gx else "chain)*"))
        placed = False
        for price_, lbl in ladder:
            if not placed and price_ <= px:
                L.append(f"- ● ${px:,.2f} — **current price**")
                placed = True
            near = " ← at price" if abs(price_ - px) / px < 0.004 else ""
            L.append(f"- {'▲' if price_ > px else '▼'} ${price_:,.2f} — {lbl}{near}")
        if not placed:
            L.append(f"- ● ${px:,.2f} — **current price**")
        L.append("")

    # -- our signals
    pat = d.get("patterns") or {}
    if pat.get("rows"):
        L.append("### Chart Patterns  *(our engine; grades from our own "
                 "39k-event replay — market-wide, not this ticker)*")
        for r in pat["rows"][:5]:
            st = pat["stats"].get(r["pattern"], {})
            grade = (f" — historically {st['hit_pct']:.0f}% hit / "
                     f"{st['win1r_pct']:.0f}% win-1R (n={st['n']:,})"
                     if st.get("n") else " — no graded history yet")
            L.append(f"- **{r['pattern']}** ({r['timeframe']}, {r['direction']}, {r['status']}) "
                     f"trigger ${r['trigger']:,.2f} → target ${r['target']:,.2f}, "
                     f"invalid ${r['invalid']:,.2f}{grade}")
        L.append("")
    osc = d.get("oscillator") or []
    if osc:
        parts = [f"{o['timeframe']}: {o['direction'] or 'neutral'}"
                 f" ({o['confluence']:.0f} confluence" +
                 (f", {', '.join(o['signals'][:3])})" if o["signals"] else ")")
                 for o in osc]
        L.append("### Oscillator  \n- " + " · ".join(parts))
        L.append("")

    # -- dealer positioning
    if gx:
        L.append(f"### Dealer Gamma  *(chain from {gx.get('as_of')})*")
        net = gx.get("net_gex_bn")
        mag = gx.get("magnitude")
        L.append(f"- Regime **{gx.get('regime')}** · net GEX "
                 f"{net:+.3f}bn ({mag}) · flip ${gx.get('gamma_flip') or 0:,.2f}"
                 f" · walls ${gx.get('put_wall') or 0:,.0f} / ${gx.get('call_wall') or 0:,.0f}")
        if mag == "decoration":
            L.append("- ⚠ Net GEX is tiny — these levels carry no real dealer "
                     "force; trade the chart, not the walls.")
        L.append("")

    # -- rotation
    if rot:
        sr = rot.get("sector_rank") or {}
        wk, mo = sr.get("weekly") or {}, sr.get("monthly") or {}
        L.append("### Rotation & Quality")
        L.append(f"- {rot.get('sector') or '?'} / {rot.get('industry') or '?'}"
                 f" · cap {_money(rot.get('market_cap'))} · RS {rot.get('rs_pct') and int(rot['rs_pct'])}/100")
        if wk or mo:
            L.append(f"- Sector rank (median stock): weekly #{wk.get('rank', '—')}"
                     f" ({_pctf(wk.get('median_ret_pct'))}), monthly #{mo.get('rank', '—')}"
                     f" ({_pctf(mo.get('median_ret_pct'))}) of 11")
        q = []
        if rot.get("rev_yoy") is not None:
            q.append(f"rev YoY {_pctf(rot['rev_yoy'] * 100)}")
        if rot.get("gross_margin") is not None:
            q.append(f"gross margin {rot['gross_margin'] * 100:.0f}%")
        if rot.get("piotroski") is not None:
            q.append(f"Piotroski {rot['piotroski']:.0f}/9")
        if rot.get("altman_z") is not None:
            q.append(f"Altman-Z {rot['altman_z']:.1f}")
        if q:
            L.append("- " + " · ".join(q))
        L.append("")

    # -- options context
    iv = d.get("iv") or {}
    if iv:
        L.append(f"### Options Context  *(as of {iv.get('as_of')})*")
        L.append(f"- ATM IV {iv.get('atm_iv') and round(iv['atm_iv'] * 100)}%"
                 f" — percentile {iv.get('iv_percentile')} of the last "
                 f"{iv.get('window_days')} sessions · put/call OI {iv.get('put_call_oi')}")
        if iv.get("skew") is not None:
            L.append(f"- Put-call skew {iv['skew'] * 100:+.1f} vol pts"
                     f" (pctile {iv.get('skew_pctile')} of its own history)"
                     " — positive = downside protection priced richer")
        L.append("")

    # -- short side (squeeze dial — context, not a trigger)
    sh = d.get("short_side") or {}
    if sh.get("squeeze_score") is not None:
        L.append("### Short Side  *(FINRA daily short volume"
                 + (f"; SI as of {sh['si_as_of']}" if sh.get("si_as_of") else "")
                 + ")*")
        bits = []
        if sh.get("si_pct_float") is not None:
            bits.append(f"SI {sh['si_pct_float']}% of float")
        if sh.get("days_to_cover") is not None:
            bits.append(f"{sh['days_to_cover']}d to cover")
        if sh.get("svr") is not None:
            bits.append(f"short-vol ratio {sh['svr']} "
                        f"(pctile {sh.get('svr_pctile')} of 60d)")
        L.append("- " + " · ".join(bits) if bits else "- partial data")
        L.append(f"- Squeeze dial: **{sh['squeeze_score']}/100 "
                 f"({sh['squeeze_label']})** — context, not a trigger; "
                 "normal market-making prints ~40-50% short daily.")
        L.append("")

    # -- insiders / social / alerts
    ins = d.get("insider") or []
    if ins:
        buys = sum(i.get("open_market_buys") or 0 for i in ins)
        sells = sum(i.get("open_market_sells") or 0 for i in ins)
        L.append(f"### Insiders  \n- Last {len(ins)} quarters: {buys} open-market "
                 f"buys vs {sells} sells (buys are the conviction signal)")
        L.append("")
    soc = d.get("social") or {}
    if soc.get("sentiment_label") or soc.get("summary"):
        L.append(f"### X / Social  \n- {soc.get('sentiment_label', '?')}"
                 + (f" — {str(soc.get('summary'))[:220]}" if soc.get("summary") else ""))
        L.append("")
    al = d.get("alerts") or []
    if al:
        L.append("### Recent Watchtower Alerts")
        for a in al[:5]:
            L.append(f"- {a['date']} {a['signal'] or a['type']}"
                     + (f" @ ${a['entry']:,.2f}" if a.get("entry") else ""))
        L.append("")

    # -- fundamentals
    fu = d.get("fundamentals") or {}
    if fu.get("quarter"):
        L.append(f"### Fundamentals  *({fu['quarter']}"
                 + (f", filed {fu['filed']}" if fu.get("filed") else "")
                 + ")*")
        line = [f"Revenue {_money(fu.get('revenue'))}"]
        if fu.get("rev_yoy_pct") is not None:
            line.append(f"{_pctf(fu['rev_yoy_pct'])} YoY")
        if fu.get("gross_margin_pct") is not None:
            line.append(f"gross margin {fu['gross_margin_pct']:.0f}%")
        if fu.get("op_margin_pct") is not None:
            line.append(f"op margin {fu['op_margin_pct']:.0f}%")
        L.append("- " + " · ".join(line))
        line2 = []
        if fu.get("net_income") is not None:
            line2.append(f"Net income {_money(fu['net_income'])}")
        if fu.get("ebitda") is not None:
            line2.append(f"EBITDA {_money(fu['ebitda'])}")
        if fu.get("fcf") is not None:
            line2.append(f"FCF {_money(fu['fcf'])}")
        if fu.get("ocf") is not None:
            line2.append(f"op cash flow {_money(fu['ocf'])}")
        if line2:
            L.append("- " + " · ".join(line2))
        line3 = []
        if fu.get("net_debt") is not None:
            nd = f"Net debt {_money(fu['net_debt'])}"
            if fu.get("net_debt_oldest") is not None and fu["net_debt_oldest"]:
                arrow = "↑" if fu["net_debt"] > fu["net_debt_oldest"] else "↓"
                nd += f" ({arrow} from {_money(fu['net_debt_oldest'])} " \
                      f"{fu.get('quarters_on_file', '?')}q ago)"
            line3.append(nd)
        if fu.get("debt_to_equity") is not None:
            line3.append(f"D/E {fu['debt_to_equity']}")
        if fu.get("roe_pct") is not None:
            line3.append(f"ROE {fu['roe_pct']:.1f}%")
        if line3:
            L.append("- " + " · ".join(line3))
    fv = fu.get("fair_value") if isinstance(fu.get("fair_value"), dict) else {}
    if fv and fv.get("fair_value"):
        L.append(f"- Fair-value model: ${fv['fair_value']:,.2f}"
                 + (f" vs price ${px:,.2f}" if px else "")
                 + (f" — {fv.get('verdict')}" if fv.get("verdict") else ""))
    elif fu.get("quarter"):
        L.append("- Fair-value model: n/a (REITs and negative-FCF names are "
                 "judged on sector metrics, not our DCF)")
    if fu.get("quarter"):
        L.append("")

    # -- market context (vol regime dial)
    vr = d.get("vol_regime") or {}
    if vr.get("vix") is not None:
        chg = f" ({vr['chg_1d_pct']:+.1f}% 1d)" if vr.get("chg_1d_pct") is not None else ""
        term = (" · ⚠ BACKWARDATION — near-term fear above far-term"
                if vr.get("term") == "backwardation"
                else " · contango" if vr.get("term") == "contango" else "")
        L.append(f"### Market Context  *(as of {vr.get('as_of')})*")
        L.append(f"- VIX {vr['vix']}{chg} — {vr.get('zone')}{term}. "
                 "A dial, not a trigger.")
        L.append("")

    # -- the read: levels, not predictions
    sup = max((s["price"] for s in (lv.get("support") or [])
               if int(s.get("stars", 0)) >= 3), default=None)
    res = min((r["price"] for r in (lv.get("resistance") or [])
               if int(r.get("stars", 0)) >= 3), default=None)
    if gx.get("magnitude") in ("load-bearing", "moderate"):
        if gx.get("put_wall") and px and gx["put_wall"] < px:
            sup = max(sup or 0, gx["put_wall"]) or sup
        if gx.get("call_wall") and px and gx["call_wall"] > px:
            res = min(res or 9e9, gx["call_wall"]) or res
    L.append("### The Read")
    if sup or res:
        line = []
        if res:
            line.append(f"constructive through ${res:,.2f}")
        if sup:
            line.append(f"defensive below ${sup:,.2f}")
        L.append("- Levels, not predictions: " + "; ".join(line) + ".")
    for e in sorted(k for k in d if k.endswith("_error")):
        L.append(f"- ⚠ {e.replace('_error', '')} unavailable: {d[e]}")
    L.append("- Freshness: prices nightly, gamma "
             + ("intraday (indexes) / " if t in ("SPY", "QQQ", "IWM", "DIA") else "")
             + "daily 8:15 AM sweep, OI settles overnight, social/alerts as stamped.")
    return "\n".join(L)
