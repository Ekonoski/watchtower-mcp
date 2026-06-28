"""
Watchtower — fundamental fair-value + quality lens.

A value anchor to complement the momentum/rotation signals: given a name you're
about to trade, is it cheap or stretched vs. an intrinsic estimate, and is the
business actually healthy underneath the move?

Two public functions, both read-time (no new ingestion — everything comes from
tables the nightly refresh already fills):

  compute_fair_value(ticker, ...) -> dict | None
      A simple 2-stage DCF on trailing free cash flow (the same shape Qualtrim's
      "stock price estimator" uses): grow FCF for `years`, decaying from the
      near-term growth rate toward a terminal rate, discount back, add a Gordon
      terminal value, divide by shares. Falls back to an EPS × exit-multiple
      estimate for names with negative FCF but positive earnings. Returns the
      fair value, upside/downside vs. live price, the method, and every
      assumption + input so the UI can show its work (and the user can override).

  fundamentals_snapshot(ticker, ...) -> dict
      ~5 years of quarterly Revenue / FCF / EPS / margins for the drawer
      sparklines, plus a synthesized "red flags" list (negative FCF, distress
      Altman Z, weak Piotroski, dilution, leverage, revenue/margin erosion).

All assumptions are deliberately conservative and transparent — this is a sanity
anchor, not a price target.
"""
from screen.reversal_screen import _conn

# Defaults — intentionally conservative. r must exceed terminal growth or the
# Gordon terminal value diverges.
DEFAULT_DISCOUNT = 0.10        # required return / WACC proxy
DEFAULT_TERMINAL_GROWTH = 0.025  # long-run ≈ nominal GDP
DEFAULT_YEARS = 10
DEFAULT_GROWTH = 0.05          # used only when analysts give us nothing
GROWTH_CAP = 0.20             # never extrapolate >20%/yr — keeps hyper-growth sane
EPS_EXIT_MULTIPLE = 18.0       # terminal P/E for the earnings-based fallback


def _f(x):
    return float(x) if x is not None else None


def _growth_from_estimates(cur, ticker):
    """Forward EPS CAGR implied by the analyst estimate ladder (earliest →
    latest fiscal year with positive EPS). None when we can't derive one."""
    cur.execute(
        """SELECT fiscal_year, eps_avg FROM analyst_estimates
           WHERE ticker = %s AND eps_avg IS NOT NULL
           ORDER BY fiscal_year ASC""",
        (ticker,),
    )
    pts = [(int(fy), float(e)) for fy, e in cur.fetchall() if e and float(e) > 0]
    if len(pts) >= 2:
        (y0, e0), (y1, e1) = pts[0], pts[-1]
        n = y1 - y0
        if n >= 1:
            return (e1 / e0) ** (1.0 / n) - 1.0
    return None


def _decayed_growth(g, terminal, t, years):
    """Growth in year t, fading linearly from g (year 1) to terminal (year N)."""
    if years < 2:
        return g
    return g + (terminal - g) * (t - 1) / (years - 1)


def _dcf_unfit(sector, industry):
    """Reason a free-cash-flow DCF is NOT meaningful for this business, else None.
    Insurers (float), banks (deposits/reserves), and REITs (FFO/NAV) all distort
    'free cash flow' — a DCF on it produces nonsense, so we don't show one."""
    i = (industry or "").lower()
    if "insurance" in i or "bank" in i or "capital markets" in i:
        return "free cash flow is distorted by float/reserves for banks & insurers"
    if (sector or "") == "Real Estate":
        return "REITs are valued on FFO/NAV, not free cash flow"
    return None


def _revenue_growth(cur, ticker):
    """Trailing revenue growth (latest TTM vs prior TTM). A fallback growth input
    for profitable-on-cash-but-not-on-EPS names. None if <8 quarters."""
    cur.execute(
        """SELECT revenue FROM fundamentals_quarterly
           WHERE ticker = %s AND revenue IS NOT NULL
           ORDER BY period_end_date DESC LIMIT 8""",
        (ticker,),
    )
    rev = [float(x[0]) for x in cur.fetchall()]
    if len(rev) < 8:
        return None
    ttm, prev = sum(rev[:4]), sum(rev[4:8])
    return (ttm / prev - 1.0) if prev > 0 else None


def compute_fair_value(ticker, discount_rate=None, growth_rate=None,
                       years=DEFAULT_YEARS, terminal_growth=DEFAULT_TERMINAL_GROWTH,
                       price_override=None):
    """Intrinsic per-share estimate + upside vs. price. None if we lack the
    inputs (no valuation row, or neither positive FCF nor positive earnings)."""
    ticker = (ticker or "").upper().strip()
    r = float(discount_rate) if discount_rate is not None else DEFAULT_DISCOUNT
    tg = float(terminal_growth)
    if r <= tg:
        r = tg + 0.02  # keep the terminal value finite
    years = max(1, min(20, int(years)))

    try:
        conn = _conn()
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT v.fcf_ttm, v.ni_ttm, v.shares_outstanding, v.price,
                          v.sector, t.industry
                   FROM valuation_metrics v JOIN tickers t ON t.ticker = v.ticker
                   WHERE v.ticker = %s
                   ORDER BY v.as_of_date DESC LIMIT 1""",
                (ticker,),
            )
            row = cur.fetchone()
            if not row:
                return None
            fcf_ttm, ni_ttm, shares, eod_price = (_f(x) for x in row[:4])
            sector, industry = row[4], row[5]
            unfit = _dcf_unfit(sector, industry)

            if growth_rate is not None:
                g_raw, g_src = float(growth_rate), "user override"
            else:
                est = _growth_from_estimates(cur, ticker)
                if est is not None:
                    g_raw, g_src = est, "analyst EPS estimates"
                else:
                    rev_g = _revenue_growth(cur, ticker)
                    if rev_g is not None:
                        g_raw, g_src = rev_g, "trailing revenue growth"
                    else:
                        g_raw, g_src = DEFAULT_GROWTH, "default (no estimates)"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    price = _f(price_override) if price_override is not None else eod_price
    price_out = round(price, 2) if price else None

    # Wrong tool for the business — show why instead of a false-precision number.
    if unfit:
        return {"fair_value": None, "price": price_out, "upside_pct": None,
                "method": "n/a", "confidence": "n/a",
                "note": f"DCF skipped — {unfit}. Judge it on book value / P-B or premium growth instead.",
                "assumptions": {}, "inputs": {"fcf_ttm": fcf_ttm, "ni_ttm": ni_ttm}}

    g = max(0.0, min(GROWTH_CAP, g_raw))
    if not shares or shares <= 0:
        return None

    fair = None
    method = None
    exit_mult = None

    if fcf_ttm and fcf_ttm > 0:
        cash, pv = fcf_ttm, 0.0
        for t in range(1, years + 1):
            cash *= (1 + _decayed_growth(g, tg, t, years))
            pv += cash / ((1 + r) ** t)
        pv += (cash * (1 + tg) / (r - tg)) / ((1 + r) ** years)  # Gordon terminal
        fair = pv / shares
        method = "DCF · trailing free cash flow"
    elif ni_ttm and ni_ttm > 0:
        eps = ni_ttm / shares
        for t in range(1, years + 1):
            eps *= (1 + _decayed_growth(g, tg, t, years))
        exit_mult = EPS_EXIT_MULTIPLE
        fair = (eps * exit_mult) / ((1 + r) ** years)
        method = "EPS × exit multiple (negative FCF fallback)"
    else:
        return None  # unprofitable on both FCF and earnings — no honest estimate

    upside = (fair / price - 1.0) if (price and price > 0) else None

    # Confidence: a simple DCF is only trustworthy when the inputs are. Flag (don't
    # hide) the cases where it tends to misfire so the UI can caveat the number.
    confidence, notes = "ok", []
    if g_src.startswith("default"):
        confidence = "low"
        notes.append("no usable growth input — generic assumption")
    if upside is not None and abs(upside) > 0.60:
        confidence = "low"
        notes.append("estimate is far from market price — a simple DCF may not fit "
                     "this business (early-stage, hyper-growth, or cyclical)")

    return {
        "fair_value": round(fair, 2),
        "price": price_out,
        "upside_pct": round(upside, 4) if upside is not None else None,
        "method": method,
        "confidence": confidence,
        "note": "; ".join(notes) if notes else None,
        "assumptions": {
            "discount_rate": round(r, 4),
            "growth_rate": round(g, 4),
            "growth_source": g_src,
            "terminal_growth": round(tg, 4),
            "years": years,
            "exit_multiple": exit_mult,
        },
        "inputs": {
            "fcf_ttm": fcf_ttm,
            "ni_ttm": ni_ttm,
            "shares_outstanding": shares,
        },
    }


def fundamentals_snapshot(ticker, quarters=20):
    """~5y of quarterly Revenue/FCF/EPS/margins for sparklines + a red-flags list."""
    ticker = (ticker or "").upper().strip()
    out = {"history": [], "red_flags": [], "ttm": {}}
    try:
        conn = _conn()
    except Exception:
        return out
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT period_end_date, revenue, free_cash_flow, net_income,
                          shares_outstanding, gross_margin, operating_margin,
                          net_debt_to_ebitda
                   FROM fundamentals_quarterly WHERE ticker = %s
                   ORDER BY period_end_date DESC LIMIT %s""",
                (ticker, quarters),
            )
            rows = cur.fetchall()[::-1]  # chronological for charting
            for (pe, rev, fcf, ni, sh, gm, om, nde) in rows:
                eps = (_f(ni) / _f(sh)) if (ni is not None and sh) else None
                out["history"].append({
                    "date": pe.isoformat() if pe else None,
                    "revenue": _f(rev), "fcf": _f(fcf),
                    "eps": round(eps, 3) if eps is not None else None,
                    "gross_margin": _f(gm), "operating_margin": _f(om),
                    "shares": _f(sh),
                })

            cur.execute(
                """SELECT altman_z_score, piotroski_score FROM financial_scores
                   WHERE ticker = %s ORDER BY as_of_date DESC LIMIT 1""",
                (ticker,),
            )
            sc = cur.fetchone()
            altman = _f(sc[0]) if sc else None
            piotroski = _f(sc[1]) if sc else None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    h = out["history"]
    flags = []
    if len(h) >= 4:
        last4 = h[-4:]
        fcf_ttm = sum((q["fcf"] or 0) for q in last4)
        rev_ttm = sum((q["revenue"] or 0) for q in last4)
        out["ttm"] = {"fcf": fcf_ttm, "revenue": rev_ttm}
        if fcf_ttm < 0:
            flags.append("Negative free cash flow (TTM)")
        # Revenue YoY (TTM vs the prior four quarters)
        if len(h) >= 8:
            rev_prev = sum((q["revenue"] or 0) for q in h[-8:-4])
            if rev_prev > 0 and rev_ttm < rev_prev:
                flags.append(f"Revenue declining YoY ({(rev_ttm/rev_prev-1)*100:.0f}%)")

    # Share dilution: latest count vs ~1 year (4 quarters) ago
    if len(h) >= 5 and h[-1]["shares"] and h[-5]["shares"]:
        dil = h[-1]["shares"] / h[-5]["shares"] - 1
        if dil > 0.03:
            flags.append(f"Share count +{dil*100:.0f}% YoY (dilution)")

    # Gross-margin compression vs ~1 year ago
    gm = [q["gross_margin"] for q in h if q["gross_margin"] is not None]
    if len(gm) >= 5 and gm[-1] < gm[-5] - 0.03:
        flags.append(f"Gross margin compressing ({(gm[-1]-gm[-5])*100:.0f}pp YoY)")

    if altman is not None and altman < 1.8:
        flags.append(f"Altman Z {altman:.1f} — distress zone (<1.8)")
    if piotroski is not None and piotroski <= 3:
        flags.append(f"Piotroski {int(piotroski)}/9 — weak fundamentals")

    out["red_flags"] = flags
    out["scores"] = {"altman_z": altman, "piotroski": piotroski}
    return out
