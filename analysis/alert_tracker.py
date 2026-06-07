"""
Watchtower — Alert performance tracker.

Logs every alert that fires, then fills in daily closing prices
for 30 trading days so we can measure win rates by signal type.

Two entry points used externally:
  log_alerts(results, alert_type)   — call from email_alerts after sending
  fill_daily_returns()              — call from scheduler at 4:45 PM ET daily
  get_performance_report()          — MCP tool: returns stats + CSV rows
"""

import logging
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Trading days to track
_FILL_DAYS = [1, 3, 5, 7, 14, 21, 30]
# Win threshold: >=5% gain counts as a win
WIN_THRESHOLD = 5.0


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
# Logging alerts
# ---------------------------------------------------------------------------

def log_alerts(results: List[dict], alert_type: str) -> int:
    """
    Insert one row per ticker into alert_log when an alert fires.
    Skips duplicates (same ticker + type + date).
    Returns number of rows inserted.
    """
    if not results:
        return 0

    conn = _conn()
    if not conn:
        return 0

    today = date.today()
    inserted = 0

    try:
        with conn.cursor() as cur:
            for r in results:
                ticker = r.get("ticker", "")
                if not ticker:
                    continue

                price = (
                    r.get("current_price")
                    or r.get("price")
                    or r.get("last_price")
                    or 0
                )
                score = r.get("score", 0)
                signal_type = (
                    r.get("signal_type")
                    or r.get("signal")
                    or r.get("combined_signal")
                    or ""
                )

                # Fetch SPY price for benchmarking
                spy_price = _fetch_current_price("SPY")

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
                         signal_type, signal_details, spy_entry_price)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (ticker, alert_type, alert_date) DO NOTHING
                """, (
                    ticker, alert_type, today,
                    float(price) if price else None,
                    float(score) if score else None,
                    signal_type or None,
                    details_json,
                    float(spy_price) if spy_price else None,
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

def fill_daily_returns() -> int:
    """
    For every alert with status='tracking', compute how many trading days
    have elapsed since alert_date, fetch the current close, and fill
    whichever d-columns are due. Marks complete after day 30.

    Returns number of alerts updated.
    """
    conn = _conn()
    if not conn:
        return 0

    today = date.today()
    updated = 0

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, alert_date, entry_price, filled_through_day,
                       spy_entry_price
                FROM alert_log
                WHERE status = 'tracking'
                  AND alert_date <= %s
                ORDER BY alert_date
            """, (today,))
            rows = cur.fetchall()

        if not rows:
            log.info("[alert_tracker] No active alerts to fill.")
            return 0

        # Count trading days between two dates
        for (alert_id, ticker, alert_date, entry_price,
             filled_through, spy_entry) in rows:

            trading_days_elapsed = _count_trading_days(alert_date, today)
            if trading_days_elapsed <= (filled_through or 0):
                continue  # nothing new to fill

            current_price = _fetch_current_price(ticker)
            spy_price = _fetch_current_price("SPY")

            if not current_price or not entry_price:
                continue

            current_day = trading_days_elapsed
            updates: Dict[str, float] = {}

            # Fill whichever milestone columns are now due
            for milestone in _FILL_DAYS:
                if (filled_through or 0) < milestone <= current_day:
                    col_price = f"d{milestone}_price"
                    col_ret = f"d{milestone}_return"
                    ret_pct = (current_price - float(entry_price)) / float(entry_price) * 100
                    updates[col_price] = current_price
                    updates[col_ret] = round(ret_pct, 4)

            if not updates:
                continue

            # Update peak
            peak_info = _compute_peak(ticker, alert_date, today, float(entry_price))

            # Build SET clause
            set_parts = [f"{k} = %s" for k in updates]
            set_parts += [
                "filled_through_day = %s",
                "d_peak_return = %s",
                "d_peak_day = %s",
            ]
            if spy_price and spy_entry:
                spy_ret = (spy_price - float(spy_entry)) / float(spy_entry) * 100
                if current_day >= 7:
                    set_parts.append("spy_d7_return = %s")
                    updates["spy_d7_return"] = round(spy_ret, 4)
                if current_day >= 30:
                    set_parts.append("spy_d30_return = %s")
                    updates["spy_d30_return"] = round(spy_ret, 4)

            is_complete = current_day >= 30
            if is_complete:
                set_parts.append("status = 'complete'")

            values = list(updates.values()) + [
                current_day,
                peak_info[0],  # peak return
                peak_info[1],  # peak day
            ]
            if "spy_d7_return" in updates:
                values.append(updates["spy_d7_return"])
            if "spy_d30_return" in updates:
                values.append(updates["spy_d30_return"])

            sql = f"UPDATE alert_log SET {', '.join(set_parts)} WHERE id = %s"
            values.append(alert_id)

            try:
                with conn.cursor() as cur2:
                    cur2.execute(sql, values)
                conn.commit()
                updated += 1
            except Exception as e:
                log.warning(f"[alert_tracker] fill row {alert_id} error: {e}")
                try:
                    conn.rollback()
                except Exception:
                    pass

        log.info(f"[alert_tracker] fill_daily_returns: {updated}/{len(rows)} alerts updated.")
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

def get_performance_report(days_back: int = 90) -> dict:
    """
    Returns a performance summary dict and CSV rows for the MCP tool.
    Covers alerts from the past `days_back` days.
    """
    conn = _conn()
    if not conn:
        return {"error": "DB unavailable", "rows": []}

    cutoff = date.today() - timedelta(days=days_back)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ticker, alert_type, alert_date, signal_type,
                    entry_price, score,
                    d1_return, d3_return, d5_return, d7_return,
                    d14_return, d21_return, d30_return,
                    d_peak_return, d_peak_day,
                    spy_d7_return, spy_d30_return,
                    status
                FROM alert_log
                WHERE alert_date >= %s
                ORDER BY alert_date DESC, d30_return DESC NULLS LAST
            """, (cutoff,))
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

    # Compute stats by alert_type
    stats = {}
    for at in ("intraday", "gem", "news"):
        subset = [r for r in rows if r["alert_type"] == at and r["d7_return"] is not None]
        if not subset:
            continue
        returns_7 = [float(r["d7_return"]) for r in subset]
        returns_30 = [float(r["d30_return"]) for r in subset if r["d30_return"] is not None]
        wins_7 = sum(1 for x in returns_7 if x >= WIN_THRESHOLD)
        wins_30 = sum(1 for x in returns_30 if x >= WIN_THRESHOLD)
        peak_returns = [float(r["d_peak_return"]) for r in subset if r["d_peak_return"] is not None]
        stats[at] = {
            "n": len(subset),
            "win_rate_d7": round(wins_7 / len(returns_7) * 100, 1) if returns_7 else None,
            "win_rate_d30": round(wins_30 / len(returns_30) * 100, 1) if returns_30 else None,
            "avg_d7_return": round(sum(returns_7) / len(returns_7), 2) if returns_7 else None,
            "avg_d30_return": round(sum(returns_30) / len(returns_30), 2) if returns_30 else None,
            "avg_peak_return": round(sum(peak_returns) / len(peak_returns), 2) if peak_returns else None,
            "best_d30": round(max(returns_30), 2) if returns_30 else None,
            "worst_d30": round(min(returns_30), 2) if returns_30 else None,
        }

    # Format rows for CSV / display
    formatted = []
    for r in rows:
        formatted.append({
            "ticker":       r["ticker"],
            "type":         r["alert_type"],
            "date":         str(r["alert_date"]),
            "signal":       r["signal_type"] or "",
            "entry":        f"${float(r['entry_price']):.2f}" if r["entry_price"] else "",
            "score":        f"{float(r['score']):.1f}" if r["score"] else "",
            "d1%":          _fmt(r["d1_return"]),
            "d3%":          _fmt(r["d3_return"]),
            "d5%":          _fmt(r["d5_return"]),
            "d7%":          _fmt(r["d7_return"]),
            "d14%":         _fmt(r["d14_return"]),
            "d21%":         _fmt(r["d21_return"]),
            "d30%":         _fmt(r["d30_return"]),
            "peak%":        _fmt(r["d_peak_return"]),
            "peak_day":     str(r["d_peak_day"]) if r["d_peak_day"] else "",
            "spy_d7%":      _fmt(r["spy_d7_return"]),
            "spy_d30%":     _fmt(r["spy_d30_return"]),
            "status":       r["status"],
        })

    return {
        "stats_by_type": stats,
        "win_threshold_pct": WIN_THRESHOLD,
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
    """Fetch latest close via Polygon snapshot."""
    try:
        from polygon import RESTClient
        api_key = os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            return None
        client = RESTClient(api_key)
        snap = client.get_snapshot_ticker("stocks", ticker)
        day = getattr(snap, "day", None)
        if day:
            return float(getattr(day, "c", None) or 0) or None
        prev = getattr(snap, "prev_day", None)
        if prev:
            return float(getattr(prev, "c", None) or 0) or None
    except Exception:
        pass
    return None


def _count_trading_days(start: date, end: date) -> int:
    """Count trading days (Mon-Fri, no holiday adjustment) between two dates."""
    if end <= start:
        return 0
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:  # Mon-Fri
            count += 1
        current += timedelta(days=1)
    return count


def _compute_peak(ticker: str, alert_date: date, today: date,
                  entry_price: float) -> Tuple[Optional[float], Optional[int]]:
    """
    Find the best closing price in the window since alert_date.
    Uses Polygon daily bars. Returns (peak_return_pct, peak_trading_day).
    """
    try:
        from polygon import RESTClient
        api_key = os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            return None, None
        client = RESTClient(api_key)
        bars = list(client.list_aggs(
            ticker,
            1, "day",
            alert_date.strftime("%Y-%m-%d"),
            today.strftime("%Y-%m-%d"),
            limit=40,
        ))
        if not bars:
            return None, None
        best_ret = None
        best_day = None
        for i, bar in enumerate(bars[1:], start=1):  # skip alert_date bar itself
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
