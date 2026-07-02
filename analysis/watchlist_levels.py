"""
Watchtower — watchlist × levels alerts.

Turns the dashboard from "find entries" into "manage positions": on every
scheduled scan, each watchlist name's live price is checked against its
STARRED multi-timeframe support/resistance ladder (analysis/levels.py — the
same levels the drawer shows), and a cross of a strong level emits a
signal-shaped row that rides the normal pipeline: dashboard table, browser
notification, email gating, alert_log performance tracking. Same pattern as
the X-velocity alerts.

Cost control — levels are expensive (~6 Polygon timeframe fetches per name),
so they're computed ONCE per ticker per ET day and cached in-process; each
scan then costs one batched snapshot call for live prices. Cross detection
compares against the previous scan's price (first sighting of the day only
sets the baseline — no alert on a stale reference), and each (ticker, level)
pair has a cooldown so a price oscillating on a level doesn't spam.

Env knobs:
  WATCHLIST_LEVEL_MIN_STARS     minimum level strength to alert on (default 4)
  WATCHLIST_LEVEL_COOLDOWN_MIN  per-(ticker,level) re-alert cooldown (default 240)
"""
import logging
import os
import time

log = logging.getLogger(__name__)

MIN_STARS = int(os.environ.get("WATCHLIST_LEVEL_MIN_STARS", "4"))
COOLDOWN_SEC = int(os.environ.get("WATCHLIST_LEVEL_COOLDOWN_MIN", "240")) * 60

# Per-process state. Bounded by watchlist size; resets on deploy (first scan
# after a restart just re-baselines — no false crosses).
_levels_cache: dict = {}   # ticker -> (et_date, [level dicts with 'side'])
_last_price: dict = {}     # ticker -> last scan's price
_alerted: dict = {}        # (ticker, level_price) -> epoch of last alert


def _et_today():
    try:
        from screen.market_calendar import et_now
        return et_now().date()
    except Exception:
        from datetime import date
        return date.today()


def _load_watchlist_tickers() -> list:
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT ticker FROM watchlist WHERE active = true")
                return [r[0] for r in cur.fetchall() if r[0]]
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"[wl-levels] watchlist load failed: {e}")
        return []


def _strong_levels(ticker: str) -> list:
    """Starred levels for a ticker, computed once per ET day."""
    today = _et_today()
    cached = _levels_cache.get(ticker)
    if cached and cached[0] == today:
        return cached[1]
    levels = []
    try:
        from analysis.levels import compute_levels
        out = compute_levels(ticker)
        if not out.get("error"):
            for side in ("support", "resistance"):
                for l in out.get(side) or []:
                    if (l.get("stars") or 0) >= MIN_STARS and l.get("price"):
                        levels.append({"price": float(l["price"]),
                                       "stars": int(l.get("stars") or 0),
                                       "timeframes": l.get("timeframes") or [],
                                       "touches": l.get("touches"),
                                       "side": side})
    except Exception as e:
        log.warning(f"[wl-levels] {ticker} level compute failed: {e}")
    _levels_cache[ticker] = (today, levels)
    return levels


def build_watchlist_level_alerts() -> list:
    """Signal-shaped rows for watchlist names that CROSSED a >=MIN_STARS level
    since the previous scan. Returns [] when there's nothing actionable."""
    tickers = _load_watchlist_tickers()
    if not tickers:
        return []

    # One batched snapshot call for live prices (same helper X-velocity uses).
    try:
        from analysis.news_scanner import _fetch_snapshot_map
        snap_map = _fetch_snapshot_map(tickers)
    except Exception as e:
        log.warning(f"[wl-levels] snapshot fetch failed: {e}")
        return []

    now = time.time()
    rows = []
    for ticker in tickers:
        snap = snap_map.get(ticker) or {}
        price = float(snap.get("price") or 0)
        if price <= 0:
            continue

        prev = _last_price.get(ticker)
        _last_price[ticker] = price
        if prev is None or prev <= 0 or prev == price:
            continue  # first sighting (baseline only) or no movement

        lo, hi = (prev, price) if price > prev else (price, prev)
        up = price > prev

        for lvl in _strong_levels(ticker):
            lp = lvl["price"]
            if not (lo < lp <= hi if up else lo <= lp < hi):
                continue  # not crossed this interval
            key = (ticker, round(lp, 2))
            if now - _alerted.get(key, 0) < COOLDOWN_SEC:
                continue  # recently alerted on this exact level
            _alerted[key] = now

            stars = lvl["stars"]
            tf_s = "·".join(lvl["timeframes"][:4]) or "multi-TF"
            touch_s = f", {lvl['touches']} touches" if lvl.get("touches") else ""
            if up:
                sig, verb = "LEVEL_BREAKOUT", ("broke above" if lvl["side"] == "resistance"
                                               else "reclaimed")
            else:
                sig, verb = "LEVEL_BREAKDOWN", ("lost" if lvl["side"] == "support"
                                                else "rejected back below")
            rows.append({
                "ticker": ticker,
                "sleeve": "watchlist_level",
                "signal_type": sig,
                "score": 75.0 if stars >= 5 else 65.0,
                "rationale": (f"Watchlist: {verb} {stars}★ level ${lp:,.2f} "
                              f"({tf_s}{touch_s}) — now ${price:,.2f}")[:200],
                "current_price": price,
                "change_pct": round(float(snap.get("change_pct") or 0), 2),
                "vol_pace_ratio": round(float(snap.get("vol_ratio") or 0), 2),
                "today_volume": int(snap.get("volume") or 0),
                "gap_pct": 0.0,
                "above_vwap": up,
                "level_price": lp,
                "level_stars": stars,
                "company_name": "",
                "sector": "",
            })
    return rows
