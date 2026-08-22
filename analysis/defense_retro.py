"""
The retro defense read (2026-08-22, Eric: "It's not possible to go back
and see if any of our previously entered trades would fit our criteria
or be avoided thus far would it?").

It is — as RESEARCH. The live shadow starts with Monday's first
volume-carrying bars because reconstruction-is-not-tape governs the
LIVE ledger; this module is the other kind of read, same as the
defense study: fetch each past touch-filled trade's entry-day 15m bars
from Polygon, run the SAME find_defense detector the live shadow uses,
and record what the signature would have said about the desk's own
fills so far. One-shot — a completion marker retires it forever, and
its verdicts live in their own table (defense_retro), never in
paper_defense_shadow, so the retro read can never masquerade as the
live record.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.defense_retro")

COMPLETE_MARKER = "defense_retro_v1"


def _shadow_r(status, defense_px, stop, exit_px):
    """R the shadow desk would have realized at the LIVE trade's exit.
    Defended -> re-priced from the defense entry; any skip -> 0 (the
    shadow never took it); anything else -> None (a hole)."""
    if status == "defended" and defense_px is not None and exit_px is not None:
        risk = float(defense_px) - float(stop)
        return (float(exit_px) - float(defense_px)) / risk if risk > 0 else None
    if status in ("knife_skipped", "missed", "no_defense"):
        return 0.0
    return None


def grade_at_exits() -> int:
    """The carry-forward (Eric, 2026-08-22: "include these findings in
    our system so we are basically moving forward with what has
    happened"): 38 of the 45 retro-graded trades were still open at
    grading time. As each live trade exits, its retro verdict grades at
    the SAME exit — defended re-priced from the defense entry, skips 0,
    holes stay holes — so the retro cohort accrues resolved comparisons
    beside the Monday-forward live shadow instead of freezing as a
    one-day report. The cohorts stay labeled: retro rows live here,
    never in paper_defense_shadow."""
    from screen.reversal_screen import _conn
    conn = _conn()
    graded = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.trade_id, d.variant, d.status, d.defense_px,
                       s.stop, t.exit_px, t.r_multiple
                FROM defense_retro d
                JOIN paper_trades t ON t.id = d.trade_id
                JOIN paper_specs s ON s.id = t.spec_id
                WHERE d.live_r IS NULL AND t.exited_at IS NOT NULL
                """
            )
            rows = cur.fetchall()
        with conn.cursor() as cur:
            for tid, variant, status, dpx, stop, exit_px, live_r in rows:
                sr = _shadow_r(status, dpx, stop, exit_px)
                cur.execute(
                    "UPDATE defense_retro SET shadow_r=%s, live_r=%s "
                    "WHERE trade_id=%s AND variant=%s",
                    (sr, live_r, tid, variant),
                )
                graded += 1
        conn.commit()
    finally:
        conn.close()
    if graded:
        log.info(f"[defense-retro] graded {graded} rows at live exits.")
    return graded


def run() -> bool:
    """Grade every past swing touch fill lacking a retro row; write the
    marker when none remain. Reads paper tables, never writes them."""
    from analysis.defense_shadow import find_defense
    from analysis.defense_study import _fetch_15m
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn

    client = get_client()
    if client is None:
        log.warning("[defense-retro] no Polygon client — skipped.")
        return False
    conn = _conn()
    done = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, s.ticker, s.setup, s.entry_trigger, s.stop,
                       t.entered_at, t.exit_px, t.r_multiple
                FROM paper_trades t JOIN paper_specs s ON s.id = t.spec_id
                WHERE s.book = 'swing' AND t.fill_kind = 'touch'
                  AND (t.entered_at AT TIME ZONE 'America/New_York')::date
                      < CURRENT_DATE
                  AND NOT EXISTS (SELECT 1 FROM defense_retro d
                                  WHERE d.trade_id = t.id)
                ORDER BY t.entered_at
                """
            )
            todo = cur.fetchall()
        if not todo:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,),
                )
            conn.commit()
            log.info(f"[defense-retro] complete — marker {COMPLETE_MARKER}.")
            return True
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        for (tid, tk, setup, trig, stop, entered_at, exit_px, live_r) in todo:
            trig, stop = float(trig), float(stop)
            day = entered_at.astimezone(et).date()
            bars = _fetch_15m(client, tk, day)
            if not bars:
                rows = [(v, "no_bars", None, None, None) for v in ("v1", "v2")]
            else:
                touch_idx = None
                for i, b in enumerate(bars):
                    if b["ts"] <= entered_at < b["ts"] + dt.timedelta(minutes=15):
                        touch_idx = i
                        break
                if touch_idx is None:
                    touch_idx = next((i for i, b in enumerate(bars)
                                      if b["low"] <= trig), None)
                if touch_idx is None:
                    rows = [(v, "no_touch", None, None, None)
                            for v in ("v1", "v2")]
                else:
                    res = find_defense(bars, trig, stop, touch_idx)
                    rows = [(v, r["status"], r.get("px"),
                             r.get("premium_pct"),
                             _shadow_r(r["status"], r.get("px"), stop,
                                       exit_px))
                            for v, r in res.items()]
            with conn.cursor() as cur:
                for variant, status, px, prem, sr in rows:
                    cur.execute(
                        """
                        INSERT INTO defense_retro
                            (trade_id, variant, ticker, setup, entry_date,
                             status, defense_px, premium_pct, live_r,
                             shadow_r)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (trade_id, variant) DO NOTHING
                        """,
                        (tid, variant, tk, setup, day, status, px, prem,
                         live_r, sr),
                    )
            conn.commit()
            done += 1
        log.info(f"[defense-retro] pass: {done} trades graded.")
        return False
    finally:
        conn.close()
