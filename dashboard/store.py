"""
Dashboard scan-snapshot store.

Every scheduled or manual scan is persisted here (Supabase `scan_snapshots`
table) so the dashboard always has the latest market picture without
re-running an expensive scan. An in-memory cache makes reads instant and
keeps the dashboard alive even if the DB is briefly unreachable.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import List, Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()
_latest_cache: Optional[dict] = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS scan_snapshots (
    id BIGSERIAL PRIMARY KEY,
    scan_type TEXT NOT NULL DEFAULT 'intraday',
    as_of TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_market_hours BOOLEAN,
    minutes_elapsed INT,
    signal_count INT NOT NULL DEFAULT 0,
    signals JSONB NOT NULL DEFAULT '[]'::jsonb,
    news JSONB NOT NULL DEFAULT '[]'::jsonb,
    market_pulse JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_scan_snapshots_as_of
    ON scan_snapshots (scan_type, as_of DESC);
"""


def _conn():
    from screen.reversal_screen import _conn as base_conn
    return base_conn()


def _json_safe(obj):
    """Drop anything that won't serialize (numpy types come through as floats via default=str)."""
    return json.loads(json.dumps(obj, default=str))


def save_scan(
    signals: List[dict],
    news: List[dict],
    market_pulse: dict,
    scan_type: str = "intraday",
) -> Optional[dict]:
    """Persist a completed scan and refresh the in-memory cache.

    Returns the snapshot dict (also cached), or None when nothing could be saved.
    """
    global _latest_cache

    is_market_hours = signals[0].get("is_market_hours") if signals else None
    minutes_elapsed = signals[0].get("minutes_elapsed") if signals else None

    snapshot = {
        "scan_type": scan_type,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "is_market_hours": is_market_hours,
        "minutes_elapsed": minutes_elapsed,
        "signal_count": len(signals or []),
        "signals": _json_safe(signals or []),
        "news": _json_safe(news or []),
        "market_pulse": _json_safe(market_pulse or {}),
    }

    with _lock:
        _latest_cache = snapshot

    conn = None
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
            cur.execute(
                """
                INSERT INTO scan_snapshots
                    (scan_type, is_market_hours, minutes_elapsed,
                     signal_count, signals, news, market_pulse)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                """,
                (
                    scan_type,
                    is_market_hours,
                    minutes_elapsed,
                    snapshot["signal_count"],
                    json.dumps(snapshot["signals"]),
                    json.dumps(snapshot["news"]),
                    json.dumps(snapshot["market_pulse"]),
                ),
            )
            # Opportunistic cleanup — snapshots have no long-term value
            # (alert_log keeps the durable signal history).
            cur.execute("DELETE FROM scan_snapshots WHERE as_of < now() - interval '14 days'")
        conn.commit()
        log.info(f"[dashboard.store] Snapshot saved: {snapshot['signal_count']} signals, "
                 f"{len(snapshot['news'])} news alerts.")
    except Exception as e:
        log.warning(f"[dashboard.store] Snapshot DB save failed (cache still updated): {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return snapshot


def get_latest(scan_type: str = "intraday") -> Optional[dict]:
    """Return the most recent snapshot — from memory if available, else from DB."""
    global _latest_cache

    with _lock:
        if _latest_cache is not None:
            return _latest_cache

    conn = None
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT as_of, is_market_hours, minutes_elapsed,
                       signal_count, signals, news, market_pulse
                FROM scan_snapshots
                WHERE scan_type = %s
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (scan_type,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        snapshot = {
            "scan_type": scan_type,
            "as_of": row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]),
            "is_market_hours": row[1],
            "minutes_elapsed": row[2],
            "signal_count": row[3],
            "signals": row[4] or [],
            "news": row[5] or [],
            "market_pulse": row[6] or {},
        }
        with _lock:
            _latest_cache = snapshot
        return snapshot
    except Exception as e:
        log.warning(f"[dashboard.store] Snapshot DB read failed: {e}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
