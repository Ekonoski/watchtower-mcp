"""
Watchtower Vol Regime — VIX + term structure as market context.

Doctrine (same spirit as the gamma magnitude rule): VIX is a DIAL, not
a trigger. The viral "VIX cheat sheets" are hindsight — 45+ fired in
September 2008 with six more down months ahead; sub-12 said "take
profits" through all of 2017's melt-up. What actually carries signal:

  zones      <13 quiet · 13-20 normal · 20-28 elevated · 28+ stress —
             context labels for how much premium/chop to expect
  term       VIX vs VIX3M. Contango (3M above front) is the market's
             resting state; BACKWARDATION (front above 3M) is rare,
             unambiguous, and the honest stress line — near-term fear
             exceeding far-term fear.
  synergy    net dealer gamma and VIX describe the same tape from two
             sides; agreement confirms the regime, divergence is
             information on its own.

Data: CBOE's own public files (FMP 402'd index history on our plan —
CBOE is the canonical source anyway and free): full daily history CSVs
plus delayed quote JSON. EOD job at 4:40 PM ET, intraday provisional
quote riding the gamma 15-minute cadence. History kept from 2021-06-01
for future backtest regime tagging.
"""
import logging
import os
from datetime import date

log = logging.getLogger(__name__)

_CBOE_HIST = ("https://cdn.cboe.com/api/global/us_indices/daily_prices/"
              "{name}_History.csv")
_CBOE_QUOTE = ("https://cdn.cboe.com/api/global/delayed_quotes/quotes/"
               "_{name}.json")
HISTORY_START = "2021-06-01"
BACKFILL_MIN_ROWS = 100    # fewer stored rows than this triggers a backfill


def _scrub(e) -> str:
    import re
    return re.sub(r"apikey=[^&\s'\"]+", "apikey=***", str(e))


def _hist(name: str, start: str) -> dict:
    """{iso_date: close} from CBOE's public history CSV (DATE,OPEN,HIGH,
    LOW,CLOSE). Tolerates both ISO and MM/DD/YYYY date styles."""
    import csv
    import io
    import requests
    resp = requests.get(_CBOE_HIST.format(name=name), timeout=60)
    resp.raise_for_status()
    out = {}
    for row in csv.reader(io.StringIO(resp.text)):
        if len(row) < 5 or not row[0] or row[0].upper().startswith("DATE"):
            continue
        raw = row[0].strip()
        try:
            if "/" in raw:                      # MM/DD/YYYY
                m, d_, y = raw.split("/")
                iso = f"{y}-{int(m):02d}-{int(d_):02d}"
            else:                               # YYYY-MM-DD
                iso = raw[:10]
            close = float(row[4])
        except (TypeError, ValueError):
            continue
        if iso >= start:
            out[iso] = close
    return out


def _quote(name: str):
    """Delayed spot from CBOE's public quote JSON; shape varies a bit
    across their endpoints, so probe the usual keys."""
    import requests
    resp = requests.get(_CBOE_QUOTE.format(name=name), timeout=15)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    for k in ("current_price", "price", "close", "last_price"):
        if data.get(k) is not None:
            return float(data[k])
    return None


def run_vix_update(intraday: bool = False) -> dict:
    """EOD: pull recent ^VIX/^VIX3M closes (full backfill when the table
    is sparse) and upsert. Intraday: one quote call to keep today's
    provisional row current — cheap enough to ride the gamma cadence."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        if intraday:
            try:
                v = _quote("VIX")
                v3 = _quote("VIX3M")
            except Exception as e:
                log.warning(f"[vix] intraday quote failed: {_scrub(e)}")
                return {"error": _scrub(e)[:100]}
            if v is None:
                return {"stored": 0}
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO vix_history (as_of, vix, vix3m)
                    VALUES (CURRENT_DATE, %s, %s)
                    ON CONFLICT (as_of) DO UPDATE SET
                        vix = EXCLUDED.vix,
                        vix3m = COALESCE(EXCLUDED.vix3m, vix_history.vix3m),
                        updated_at = now()
                """, (v, v3))
            conn.commit()
            return {"stored": 1, "vix": v, "vix3m": v3}

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM vix_history")
            sparse = (cur.fetchone()[0] or 0) < BACKFILL_MIN_ROWS
        from datetime import timedelta
        start = HISTORY_START if sparse else \
            (date.today() - timedelta(days=15)).isoformat()
        try:
            vix = _hist("VIX", start)
            v3m = _hist("VIX3M", start)
        except Exception as e:
            log.warning(f"[vix] history fetch failed: {_scrub(e)}")
            return {"error": _scrub(e)[:100]}
        rows = [(d, vix[d], v3m.get(d)) for d in sorted(vix)]
        if not rows:
            return {"stored": 0}
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO vix_history (as_of, vix, vix3m)
                VALUES (%s, %s, %s)
                ON CONFLICT (as_of) DO UPDATE SET
                    vix = EXCLUDED.vix,
                    vix3m = COALESCE(EXCLUDED.vix3m, vix_history.vix3m),
                    updated_at = now()
            """, rows)
        conn.commit()
        log.info(f"[vix] stored {len(rows)} sessions "
                 f"({'backfill' if sparse else 'update'})")
        return {"stored": len(rows), "backfill": sparse}
    finally:
        conn.close()


def zone(v: float) -> str:
    if v is None:
        return "n/a"
    return ("quiet" if v < 13 else "normal" if v < 20
            else "elevated" if v < 28 else "stress")


def get_vix_context() -> dict:
    """Latest reading + zone + term structure + 1-day change — the dial
    the pulse bar, brief, and morning read share. {} when unpopulated."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT as_of, vix, vix3m,
                       lag(vix) OVER (ORDER BY as_of) AS prev
                FROM vix_history ORDER BY as_of DESC LIMIT 2
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows or rows[0][1] is None:
        return {}
    as_of, v, v3, _ = rows[0]
    prev = float(rows[1][1]) if len(rows) > 1 and rows[1][1] is not None else None
    v = float(v)
    v3 = float(v3) if v3 is not None else None
    term = None
    if v3:
        term = "backwardation" if v > v3 else "contango"
    return {
        "as_of": str(as_of), "vix": round(v, 2),
        "vix3m": round(v3, 2) if v3 else None,
        "zone": zone(v), "term": term,
        "chg_1d": round(v - prev, 2) if prev is not None else None,
        "chg_1d_pct": round((v / prev - 1) * 100, 1) if prev else None,
    }
