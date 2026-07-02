"""
Watchtower — Alert performance tracker.

Logs every alert/signal that fires, then fills in daily closing prices
so we can measure win rates and adjust score thresholds over time.

Tracking windows by signal type:
  intraday   → 30 trading days
  news       → 14 trading days
  gem        → 60 trading days
  reversal   → 90 trading days
  momentum   → 90 trading days
  breakdown  → 60 trading days
  insider    → 60 trading days
  master     → 90 trading days

External entry points:
  log_alerts(results, alert_type)   — call after any scan/send
  fill_daily_returns()              — scheduler 4:45 PM ET daily
  get_performance_report()          — MCP tool: summary stats + CSV
"""

import logging
import os
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Milestones tracked per type — subset of all possible columns
_ALL_MILESTONES = [1, 3, 5, 7, 14, 21, 30, 45, 60, 90]

# Per alert-type: how many trading days to track
TRACK_DAYS: Dict[str, int] = {
    "intraday":  30,
    "news":      14,
    "gem":       60,
    "reversal":  90,
    "momentum":  90,
    "breakdown": 60,
    "insider":   60,
    "master":    90,
}

# Win thresholds (always in the direction of the trade):
#   Day-trade signals (intraday, news): >=2% by the NEXT CLOSE (d1) — the
#   question being answered is "did it move enough, fast enough, to day trade".
#   Swing screens (reversal/momentum/gems/...): >=5% within the type's window.
WIN_THRESHOLD = float(os.environ.get("ALERT_WIN_THRESHOLD", "5.0"))
DAY_WIN_THRESHOLD = float(os.environ.get("ALERT_DAY_WIN_THRESHOLD", "2.0"))
DAY_TRADE_TYPES = {"intraday", "news"}

# Bearish signals win when the stock FALLS. Their returns are sign-flipped in
# the performance report so every stat reads "return in trade direction".
BEARISH_SIGNAL_TYPES = {
    "VWAP_REJECTION", "INTRADAY_BREAKDOWN", "GAP_DOWN_CONFIRM", "DISTRIBUTION",
    "SELL", "STRONG_SELL",
}


def _is_bearish(alert_type: str, signal_type) -> bool:
    if alert_type == "breakdown":
        return True
    return (signal_type or "").upper() in BEARISH_SIGNAL_TYPES


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def _conn():
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from screen.reversal_screen import _conn as _base_conn
        return _base_conn()
    except Exception as e:
        log.warning(f"[alert_tracker] DB connection failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Log alerts
# ---------------------------------------------------------------------------

def log_alerts(results: List[dict], alert_type: str) -> int:
    """
    Insert one row per ticker into alert_log when a signal fires.
    Skips duplicates (same ticker + type + date).
    Returns number of rows inserted.
    """
    if not results:
        return 0

    conn = _conn()
    if not conn:
        return 0

    today = date.today()
    track_days = TRACK_DAYS.get(alert_type, 30)
    inserted = 0

    try:
        spy_price = _fetch_current_price("SPY")

        with conn.cursor() as cur:
            for r in results:
                # News alerts carry the symbol as primary_ticker, not ticker
                ticker = r.get("ticker") or r.get("primary_ticker") or ""
                if not ticker:
                    continue

                price = (
                    r.get("current_price")
                    or r.get("price")
                    or r.get("last_price")
                    or 0
                )
                # Each sleeve names its score differently — check them all,
                # or the column logs blank for that screen's signals.
                score = (
                    r.get("score")
                    or r.get("reversal_score")
                    or r.get("momentum_score")
                    or r.get("breakdown_score")
                    or r.get("signal_score")
                    or r.get("composite_score")
                    or 0
                )
                # News alerts carry magnitude instead of a numeric score
                if not score and r.get("magnitude"):
                    score = {"high": 80, "medium": 60, "low": 40}.get(r["magnitude"], 0)
                signal_type = (
                    r.get("signal_type")
                    or r.get("signal")
                    or r.get("sleeve")
                    or r.get("combined_signal")
                    or alert_type
                )

                import json as _json
                try:
                    details_json = _json.dumps({
                        k: v for k, v in r.items()
                        if isinstance(v, (str, int, float, bool, type(None)))
                    })
                except Exception:
                    details_json = "{}"

                cur.execute("""
                    INSERT INTO alert_log
                        (ticker, alert_type, alert_date, entry_price, score,
                         signal_type, signal_details, spy_entry_price, track_days)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (ticker, alert_type, alert_date) DO NOTHING
                """, (
                    ticker, alert_type, today,
                    float(price) if price else None,
                    float(score) if score else None,
                    signal_type or None,
                    details_json,
                    float(spy_price) if spy_price else None,
                    track_days,
                ))
                if cur.rowcount:
                    inserted += 1

        conn.commit()
        log.info(f"[alert_tracker] Logged {inserted} {alert_type} alerts for {today}.")
    except Exception as e:
        log.error(f"[alert_tracker] log_alerts error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return inserted


# ---------------------------------------------------------------------------
# Daily return fill
# ---------------------------------------------------------------------------

def _fetch_daily_closes(ticker: str, start: date, end: date) -> list:
    """Daily closes from alert day through today: [(date, close), ...].
    bars[0] is the alert day, bars[m] is trading day m after the alert."""
    try:
        from polygon import RESTClient
        api_key = os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            return []
        client = RESTClient(api_key)
        bars = list(client.list_aggs(
            ticker, 1, "day",
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            limit=200,
        ))
        out = []
        for b in bars:
            close = getattr(b, "close", None) or getattr(b, "c", None)
            ts = getattr(b, "timestamp", None)
            if close:
                out.append((ts, float(close)))
        return out
    except Exception as e:
        log.warning(f"[alert_tracker] daily bars fetch failed for {ticker}: {str(e)[:100]}")
        return []


def fill_daily_returns() -> int:
    """
    For every alert with status='tracking', fetch daily bars since the alert
    and fill each due milestone with THAT day's actual close — correct even
    when a fill runs late (the old version stamped 'price at fill time' on
    every due milestone, so a missed day corrupted d1 with d2's price).

    Marks complete once filled_through_day >= track_days.
    Returns number of alerts updated.
    """
    conn = _conn()
    if not conn:
        return 0

    today = date.today()
    updated = 0
    bar_fetch_failures = 0

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, alert_type, alert_date, entry_price,
                       filled_through_day, spy_entry_price, track_days, signal_type
                FROM alert_log
                WHERE status = 'tracking'
                  AND alert_date < %s
                ORDER BY alert_date
            """, (today,))
            rows = cur.fetchall()

        if not rows:
            log.info("[alert_tracker] No active alerts to fill.")
            return 0

        # SPY closes per alert_date, fetched once per distinct date
        spy_closes_cache: Dict[date, list] = {}

        for (alert_id, ticker, alert_type, alert_date, entry_price,
             filled_through, spy_entry, track_days, signal_type) in rows:

            filled_through = filled_through or 0
            if not entry_price:
                continue

            closes = _fetch_daily_closes(ticker, alert_date, today)
            if len(closes) < 2:
                bar_fetch_failures += 1
                continue

            entry = float(entry_price)
            current_day = len(closes) - 1  # trading days elapsed, per the actual market calendar
            if current_day <= filled_through:
                continue
            max_days = track_days or TRACK_DAYS.get(alert_type, 30)

            # Determine which milestones are now due
            due_milestones = [
                m for m in _ALL_MILESTONES
                if m <= max_days and filled_through < m <= current_day
            ]
            if not due_milestones:
                continue

            updates: Dict[str, float] = {}
            for milestone in due_milestones:
                close_m = closes[milestone][1]
                updates[f"d{milestone}_price"] = close_m
                updates[f"d{milestone}_return"] = round((close_m - entry) / entry * 100, 4)

            # SPY benchmark at the same milestones
            if spy_entry:
                if alert_date not in spy_closes_cache:
                    spy_closes_cache[alert_date] = _fetch_daily_closes("SPY", alert_date, today)
                spy_closes = spy_closes_cache[alert_date]
                spy_entry_f = float(spy_entry)

                def _spy_ret_at(m):
                    if spy_entry_f and len(spy_closes) > m:
                        return round((spy_closes[m][1] - spy_entry_f) / spy_entry_f * 100, 4)
                    return None

                for m in due_milestones:
                    if m >= 7 and _spy_ret_at(7) is not None and 7 <= current_day:
                        updates["spy_d7_return"] = _spy_ret_at(7)
                    if m >= 30 and _spy_ret_at(30) is not None and 30 <= current_day:
                        updates["spy_d30_return"] = _spy_ret_at(30)

            # Peak tracking from the same bars: best close after the alert day,
            # measured IN TRADE DIRECTION. For bearish signals the favorable
            # move is DOWN, so the peak is the largest (entry - close)/entry —
            # the long-only max() used to report a short's worst adverse move
            # as its "peak". d_peak_return is therefore stored
            # direction-adjusted (positive = trade worked), matching how the
            # report and UI already display it without sign-flipping.
            bearish = _is_bearish(alert_type, signal_type)
            peak_ret, peak_day = None, None
            for i, (_, c) in enumerate(closes[1:], start=1):
                r = (c - entry) / entry * 100
                if bearish:
                    r = -r
                if peak_ret is None or r > peak_ret:
                    peak_ret, peak_day = round(r, 4), i
            is_complete = current_day >= max_days

            # Build dynamic UPDATE
            set_parts = [f"{k} = %s" for k in updates]
            set_parts += ["filled_through_day = %s", "d_peak_return = %s", "d_peak_day = %s"]
            if is_complete:
                set_parts.append("status = 'complete'")

            values = list(updates.values()) + [current_day, peak_ret, peak_day]
            values.append(alert_id)

            sql = f"UPDATE alert_log SET {', '.join(set_parts)} WHERE id = %s"

            try:
                with conn.cursor() as cur2:
                    cur2.execute(sql, values)
                conn.commit()
                updated += 1
            except Exception as e:
                log.warning(f"[alert_tracker] fill row {alert_id} ({ticker}): {e}")
                try:
                    conn.rollback()
                except Exception:
                    pass

        log.info(f"[alert_tracker] fill_daily_returns: {updated}/{len(rows)} alerts updated"
                 f"{f', {bar_fetch_failures} bar-fetch failures' if bar_fetch_failures else ''}.")
    except Exception as e:
        log.error(f"[alert_tracker] fill_daily_returns error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return updated


# ---------------------------------------------------------------------------
# Performance report
# ---------------------------------------------------------------------------

def get_performance_report(days_back: int = 90, alert_type: str = None) -> dict:
    """
    Returns a performance summary dict and rows for the MCP tool.
    Optionally filter by alert_type. Covers the last `days_back` days.
    """
    conn = _conn()
    if not conn:
        return {"error": "DB unavailable", "rows": []}

    cutoff = date.today() - timedelta(days=days_back)

    try:
        with conn.cursor() as cur:
            base_sql = """
                SELECT
                    ticker, alert_type, alert_date, alerted_at, signal_type,
                    entry_price, score,
                    d1_return, d3_return, d5_return, d7_return,
                    d14_return, d21_return, d30_return,
                    d45_return, d60_return, d90_return,
                    d_peak_return, d_peak_day,
                    spy_d7_return, spy_d30_return,
                    track_days, status
                FROM alert_log
                WHERE alert_date >= %s
            """
            params = [cutoff]
            if alert_type:
                base_sql += " AND alert_type = %s"
                params.append(alert_type)
            base_sql += " ORDER BY alert_date DESC, COALESCE(d30_return, d7_return) DESC NULLS LAST"

            cur.execute(base_sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        return {"error": str(e), "rows": []}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not rows:
        return {"summary": "No alerts logged yet.", "rows": []}

    # Compute stats grouped by alert_type
    stats = {}
    for at in TRACK_DAYS:
        subset = [r for r in rows if r["alert_type"] == at]
        if not subset:
            continue

        max_td = TRACK_DAYS[at]
        is_day_trade = at in DAY_TRADE_TYPES
        threshold = DAY_WIN_THRESHOLD if is_day_trade else WIN_THRESHOLD
        # Use the longest filled milestone as the primary return column;
        # day-trade types measure the short window at next close (d1).
        primary_col = f"d{max_td}_return" if max_td <= 90 else "d90_return"
        short_col = "d1_return" if is_day_trade else "d7_return"

        filled_primary = [r for r in subset if r.get(primary_col) is not None]
        filled_short = [r for r in subset if r.get(short_col) is not None]

        def avg(vals): return round(sum(vals) / len(vals), 2) if vals else None
        def win_rate(vals): return round(sum(1 for x in vals if x >= threshold) / len(vals) * 100, 1) if vals else None

        def _adj(r, col):
            """Return in the direction of the trade: flipped for bearish signals."""
            v = float(r[col])
            return -v if _is_bearish(r["alert_type"], r.get("signal_type")) else v

        primary_rets = [_adj(r, primary_col) for r in filled_primary]
        short_rets = [_adj(r, short_col) for r in filled_short]
        peak_rets = [float(r["d_peak_return"]) for r in subset if r.get("d_peak_return") is not None]

        stats[at] = {
            "n_total": len(subset),
            "n_filled": len(filled_primary),
            "track_days": max_td,
            "win_threshold_pct": threshold,
            "is_day_trade": is_day_trade,
            "win_rate_short": win_rate(short_rets),
            "win_rate_full": win_rate(primary_rets),
            "avg_short_return": avg(short_rets),
            "avg_full_return": avg(primary_rets),
            "avg_peak_return": avg(peak_rets),
            "best": round(max(primary_rets), 2) if primary_rets else None,
            "worst": round(min(primary_rets), 2) if primary_rets else None,
            "short_label": "D1" if is_day_trade else "D7",
            "full_label": f"D{max_td}",
        }

    # Format rows for display/CSV
    formatted = []
    for r in rows:
        at = r["alert_type"]
        td = TRACK_DAYS.get(at, 30)
        time_et = ""
        ts = r.get("alerted_at")
        if ts is not None:
            try:
                import pytz
                t = ts.astimezone(pytz.timezone("America/New_York"))
                # Backfilled rows sit at midnight — show time only when real
                if t.hour or t.minute:
                    time_et = t.strftime("%H:%M")
            except Exception:
                pass
        formatted.append({
            "ticker":     r["ticker"],
            "type":       at,
            "dir":        "short" if _is_bearish(at, r.get("signal_type")) else "long",
            "date":       str(r["alert_date"]),
            "time":       time_et,
            "signal":     r["signal_type"] or "",
            "entry":      f"${float(r['entry_price']):.2f}" if r["entry_price"] else "",
            "score":      f"{float(r['score']):.1f}" if r["score"] else "",
            "d1%":        _fmt(r.get("d1_return")),
            "d3%":        _fmt(r.get("d3_return")),
            "d7%":        _fmt(r.get("d7_return")),
            "d14%":       _fmt(r.get("d14_return")),
            "d30%":       _fmt(r.get("d30_return")),
            "d60%":       _fmt(r.get("d60_return")) if td >= 60 else "n/a",
            "d90%":       _fmt(r.get("d90_return")) if td >= 90 else "n/a",
            "peak%":      _fmt(r.get("d_peak_return")),
            "peak_day":   str(r["d_peak_day"]) if r.get("d_peak_day") else "",
            "spy_d7%":    _fmt(r.get("spy_d7_return")),
            "spy_d30%":   _fmt(r.get("spy_d30_return")),
            "track_days": str(td),
            "status":     r["status"],
        })

    return {
        "stats_by_type": stats,
        "win_threshold_pct": WIN_THRESHOLD,
        "day_win_threshold_pct": DAY_WIN_THRESHOLD,
        "days_back": days_back,
        "total_alerts": len(rows),
        "rows": formatted,
    }


def generate_csv(report: dict) -> str:
    """Convert report rows to CSV string."""
    rows = report.get("rows", [])
    if not rows:
        return "No data."
    headers = list(rows[0].keys())
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(str(r.get(h, "")) for h in headers))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_current_price(ticker: str) -> Optional[float]:
    try:
        from polygon import RESTClient
        api_key = os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            return None
        client = RESTClient(api_key)
        snap = client.get_snapshot_ticker("stocks", ticker)
        day = getattr(snap, "day", None)
        if day:
            price = float(getattr(day, "c", None) or 0)
            if price:
                return price
        prev = getattr(snap, "prev_day", None)
        if prev:
            price = float(getattr(prev, "c", None) or 0)
            if price:
                return price
    except Exception:
        pass
    return None


def _count_trading_days(start: date, end: date) -> int:
    if end <= start:
        return 0
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def _compute_peak(ticker: str, alert_date: date, today: date,
                  entry_price: float) -> Tuple[Optional[float], Optional[int]]:
    try:
        from polygon import RESTClient
        api_key = os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            return None, None
        client = RESTClient(api_key)
        bars = list(client.list_aggs(
            ticker, 1, "day",
            alert_date.strftime("%Y-%m-%d"),
            today.strftime("%Y-%m-%d"),
            limit=120,
        ))
        if not bars:
            return None, None
        best_ret = None
        best_day = None
        for i, bar in enumerate(bars[1:], start=1):
            close = float(getattr(bar, "c", 0) or 0)
            if not close:
                continue
            ret = (close - entry_price) / entry_price * 100
            if best_ret is None or ret > best_ret:
                best_ret = ret
                best_day = i
        return (round(best_ret, 4) if best_ret is not None else None, best_day)
    except Exception:
        return None, None


def _fmt(val) -> str:
    if val is None:
        return ""
    return f"{float(val):+.2f}%"
