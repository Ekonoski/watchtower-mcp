"""
Watchtower — Sector heat / theme scorer (GMMSS support for up-and-comers).

Multi-factor aggregator (price mom + vol surge + DB alt data: analyst revisions momentum,
news sentiment delta, social buzz rank surge) to identify "heating" sectors for momentum/
up-and-comer bias and reversal bias toward quality in hot themes.

Usage:
    from screen.sector_heat import compute_sector_heat, sector_heat_boost_for_ticker
    heat = compute_sector_heat(prices, quality)
    boost = sector_heat_boost_for_ticker(ticker, fundamentals, heat)

This keeps the "ahead of the curve on sectors becoming hotter" explicit and reusable.
"""
from typing import Dict, Any
import numpy as np
import pandas as pd

try:
    from psycopg2.extras import RealDictCursor
except Exception:
    RealDictCursor = None

# Reuse DB connection from reversal for alt data (news, revisions, social per sector)
try:
    from reversal_screen import _conn
except Exception:
    _conn = None


def compute_sector_heat(prices: Dict[str, pd.DataFrame],
                        quality: Dict[str, dict],
                        min_tickers_per_sector: int = 3) -> Dict[str, dict]:
    """
    Compute per-sector heat from recent price action + alt data (GMMSS multi-factor for up-and-comers).
    Price mom + vol surge (from passed prices) + DB: avg revision momentum, sentiment change, social rank surge.
    Returns richer dict per sector with composite 'heat' 0-1 (higher = hotter sector).
    """
    # --- Price-based (always available from passed data) ---
    sector_data: Dict[str, Dict[str, list]] = {}
    for t, df in prices.items():
        if t == "SPY" or df is None or len(df) < 22:
            continue
        q = quality.get(t, {})
        sec = q.get("sector") or "Unknown"
        c = df["close"].astype(float)
        v = df.get("volume", pd.Series([1.0]*len(c))).astype(float)
        ret20 = (c.iloc[-1] / c.iloc[-21] - 1.0) if c.iloc[-21] != 0 else 0.0
        recent_v = v.tail(10).mean() if len(v) >= 10 else v.mean()
        overall_v = v.mean() or 1.0
        vsurge = recent_v / overall_v if overall_v > 0 else 1.0
        sector_data.setdefault(sec, {"rets": [], "vsurges": [], "count": 0})
        sector_data[sec]["rets"].append(ret20)
        sector_data[sec]["vsurges"].append(vsurge)
        sector_data[sec]["count"] += 1

    out = {}
    for sec, d in sector_data.items():
        if d["count"] < min_tickers_per_sector:
            continue
        avg_ret = float(np.mean(d["rets"]))
        avg_v = float(np.mean(d["vsurges"]))
        heat = 0.0
        if avg_ret > 0.12: heat += 0.45
        elif avg_ret > 0.06: heat += 0.30
        elif avg_ret > 0.02: heat += 0.15
        if avg_v > 1.25: heat += 0.25
        elif avg_v > 1.10: heat += 0.15
        heat = min(1.0, heat + 0.1)
        out[sec] = {
            "avg_ret20": round(avg_ret, 4),
            "avg_vol_surge": round(avg_v, 3),
            "n": d["count"],
            "heat": round(heat, 3),
            "price_heat": round(heat, 3),
        }

    # --- Multi-factor alt data from DB (news sentiment, analyst revisions, social buzz) ---
    alt: Dict[str, dict] = {}
    conn = None
    if _conn and RealDictCursor:
        try:
            conn = _conn()
            # News sentiment delta per sector (positive change = heating)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT t.sector,
                           AVG(COALESCE(ns.sentiment_change_pct, 0)) AS avg_sent_chg,
                           COUNT(*) AS n
                    FROM news_sentiment ns
                    JOIN tickers t ON t.ticker = ns.ticker
                    WHERE ns.as_of_date = (SELECT MAX(as_of_date) FROM news_sentiment)
                      AND t.delisted = false
                    GROUP BY t.sector
                """)
                for r in cur.fetchall():
                    sec = r["sector"] or "Unknown"
                    alt.setdefault(sec, {})["avg_sent_chg"] = float(r["avg_sent_chg"] or 0)
                    alt[sec]["n_sent"] = int(r["n"] or 0)

            # Analyst revisions momentum per sector
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT t.sector,
                           AVG(COALESCE(ar.revision_30d_pct, 0)) AS avg_rev,
                           COUNT(*) AS n
                    FROM analyst_revisions ar
                    JOIN tickers t ON t.ticker = ar.ticker
                    WHERE ar.as_of_date = (SELECT MAX(as_of_date) FROM analyst_revisions)
                      AND t.delisted = false
                    GROUP BY t.sector
                """)
                for r in cur.fetchall():
                    sec = r["sector"] or "Unknown"
                    alt.setdefault(sec, {})["avg_rev_30d"] = float(r["avg_rev"] or 0)
                    alt[sec]["n_rev"] = int(r["n"] or 0)

            # Social buzz rank surge per sector (from all-stocks aggregate)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    WITH latest AS (SELECT MAX(snapshot_date) AS d FROM social_buzz),
                    buzz AS (
                        SELECT s.ticker, COALESCE(s.rank_surge, 0) AS rank_surge, t.sector
                        FROM social_buzz s, latest l, tickers t
                        WHERE s.snapshot_date = l.d
                          AND s.source = 'all-stocks'
                          AND s.mentions >= 10
                          AND t.ticker = s.ticker
                          AND t.delisted = false
                    )
                    SELECT sector, AVG(rank_surge) AS avg_social_surge, COUNT(*) AS n
                    FROM buzz
                    GROUP BY sector
                """)
                for r in cur.fetchall():
                    sec = r["sector"] or "Unknown"
                    alt.setdefault(sec, {})["avg_social_surge"] = float(r["avg_social_surge"] or 0)
                    alt[sec]["n_social"] = int(r["n"] or 0)
        except Exception:
            pass
        finally:
            if conn:
                try: conn.close()
                except: pass

    # Combine into final heat (price 40%, revisions 30%, sentiment 20%, social 10%)
    for sec in list(out.keys()):
        h = out[sec]
        a = alt.get(sec, {})
        p_heat = h["heat"]
        rev = a.get("avg_rev_30d", 0)
        sent = a.get("avg_sent_chg", 0)
        soc = a.get("avg_social_surge", 0)

        # simple normalize to 0-1 contributions
        rev_c = max(0, min(1, (rev + 0.05) / 0.15)) if rev else 0.5
        sent_c = max(0, min(1, (sent + 2) / 5)) if sent else 0.5
        soc_c = max(0, min(1, (soc + 50) / 150)) if soc else 0.5

        final_heat = (0.40 * p_heat +
                      0.30 * rev_c +
                      0.20 * sent_c +
                      0.10 * soc_c)
        h["heat"] = round(min(1.0, final_heat), 3)
        h["alt_heat"] = round(0.30*rev_c + 0.20*sent_c + 0.10*soc_c, 3)
        h["avg_revision_30d"] = round(rev, 4)
        h["avg_sentiment_chg"] = round(sent, 3)
        h["avg_social_surge"] = round(soc, 1)
        h["n_alt"] = a.get("n_rev") or a.get("n_sent") or a.get("n_social") or 0

    # re-rank
    ranked = sorted(out.items(), key=lambda kv: kv[1]["heat"], reverse=True)
    for rank, (sec, v) in enumerate(ranked, 1):
        out[sec]["rank"] = rank
    return out


def sector_heat_boost_for_ticker(ticker: str, quality: dict, sector_heat: dict) -> float:
    """Return a small additive boost (points on 0-100 score) for a ticker in a hot sector."""
    sec = (quality or {}).get("sector") or "Unknown"
    h = sector_heat.get(sec, {})
    heat = h.get("heat", 0.0)
    if heat >= 0.7:
        return 4.0
    if heat >= 0.5:
        return 2.5
    if heat >= 0.3:
        return 1.2
    return 0.0
