"""
daily_prices sanity sweep (2026-09-04 — the SPY 2005-05-27 row carried a
high of 1120.2 on a 120 close, a vendor fat-finger that every SPY
all-time-high query read as real; Eric: "are you gonna refetch that?").

Rule: a stored daily bar whose open, high, or low sits more than 2x from
its close (or high < low) is SUSPECT. Every suspect row is re-fetched
from Polygon and REPLACED with the vendor's current bar — the sweep never
hand-edits a price and never invents one. Verdicts, one row per suspect
in `price_sanity`:

  corrected      vendor bar differs from the stored one -> stored row was bad, now replaced
  confirmed      vendor agrees -> a real move (warrants, penny names, halts); left as is
  no_vendor_bar  vendor returned nothing for that day -> a hole; stored row left as is

Most suspects are real (a warrant going 0.38 -> 12.06 is a print, not a
typo), so the sweep is a re-verification, not a purge. Boot seeder with
per-row resume (the price_sanity row IS the claim) and a completion
marker; suspects that appear later (a new bad vendor print) are picked
up by the next boot because the marker is re-earned whenever the suspect
set has unverified members.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.price_sanity")

COMPLETE_MARKER = "price_sanity_v1"
RATIO = 2.0
BUDGET_S = 10 * 60

SUSPECT_SQL = """
    SELECT d.ticker, d.trade_date, d.open, d.high, d.low, d.close
    FROM daily_prices d
    LEFT JOIN price_sanity s ON s.ticker = d.ticker AND s.trade_date = d.trade_date
    WHERE s.ticker IS NULL AND d.close > 0 AND d.low > 0
      AND (d.high > %(r)s * d.close OR d.low < d.close / %(r)s OR d.high < d.low
           OR d.open > %(r)s * d.close OR d.open < d.close / %(r)s)
    ORDER BY d.trade_date DESC
"""


def is_suspect(o, h, l, c, ratio=RATIO) -> bool:
    """Pure. The same rule as SUSPECT_SQL, for tests and callers."""
    if c is None or l is None or c <= 0 or l <= 0:
        return False
    return (h > ratio * c or l < c / ratio or h < l
            or (o is not None and (o > ratio * c or o < c / ratio)))


def verdict(old, new) -> str:
    """Pure. old/new = (open, high, low, close); new None = no vendor bar."""
    if new is None:
        return "no_vendor_bar"
    for a, b in zip(old, new):
        if a is None or b is None:
            if a != b:
                return "corrected"
            continue
        if abs(float(a) - float(b)) > 1e-6 * max(1.0, abs(float(b))):
            return "corrected"
    return "confirmed"


def run() -> bool:
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute(SUSPECT_SQL, {"r": RATIO})
            todo = c.fetchall()
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                          "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
            conn.commit()
            return True
        client = get_client()
        if client is None:
            log.warning("[price_sanity] no Polygon client; %d suspects unverified (hole).", len(todo))
            return False
        n = {"corrected": 0, "confirmed": 0, "no_vendor_bar": 0}
        for tk, d, o, h, l, cl in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[price_sanity] budget hit; %s this pass, resuming.", n)
                return False
            try:
                aggs = list(client.get_aggs(tk, 1, "day", d.isoformat(), d.isoformat(), limit=5))
            except Exception as e:
                log.warning(f"[price_sanity] {tk} {d} fetch failed: {e}")
                continue
            new = None
            for a in aggs:
                ad = dt.datetime.fromtimestamp(a.timestamp / 1000, dt.timezone.utc).date()
                if ad == d:
                    new = (float(a.open), float(a.high), float(a.low), float(a.close))
                    break
            v = verdict((o, h, l, cl), new)
            with conn.cursor() as c:
                if v == "corrected":
                    c.execute("UPDATE daily_prices SET open=%s, high=%s, low=%s, close=%s "
                              "WHERE ticker=%s AND trade_date=%s", (*new, tk, d))
                c.execute("""INSERT INTO price_sanity (ticker, trade_date, old_open, old_high, old_low,
                                 old_close, new_open, new_high, new_low, new_close, verdict)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                             ON CONFLICT (ticker, trade_date) DO UPDATE SET
                               new_open=EXCLUDED.new_open, new_high=EXCLUDED.new_high,
                               new_low=EXCLUDED.new_low, new_close=EXCLUDED.new_close,
                               verdict=EXCLUDED.verdict, checked_at=now()""",
                          (tk, d, o, h, l, cl, *(new or (None, None, None, None)), v))
            conn.commit()
            n[v] += 1
            if v == "corrected":
                log.warning(f"[price_sanity] {tk} {d}: CORRECTED O/H/L/C {o}/{h}/{l}/{cl} -> "
                            f"{new[0]}/{new[1]}/{new[2]}/{new[3]}")
        log.info("[price_sanity] pass complete: %s", n)
        return False            # marker earns on the next pass, when the suspect set is empty
    finally:
        conn.close()
