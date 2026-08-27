"""
Fill forensics for 2026-08-27 (Eric: "so the trade was actually accurate
and the tape was correct? those trades are valid?").

Two of the morning's three SPY gamma fills could not be certified from
the stored 15m tape:

  - trade 87 (wall_fade_770 short, entered 10:30:44 @ 769.675): no
    COMPLETED 15m bar had printed 770 by then (max high 769.88). The
    code explanation found same day: _touch() carries a 0.1% tolerance
    (~77 cents on SPY), so the 10:15 bar's 769.57 close "touched" the
    wall. Code-correct — but the question whether the WALL ITSELF
    printed before entry deserves a tape answer, because it decides
    whether the tolerance is a feature (catching the stall) or a leak
    (shorting before the level is even tested).
  - trade 86 (flip_hold_768.12 long, entered 10:15:39 @ 768.655): the
    touch-bar's stored close is 768.935; the fill recorded 768.655 —
    two fetches seconds apart returned different closes for the same
    completed bar (vendor-side settling), and first-seen persistence
    kept only one of them.

This one-shot job (marker fill_audit_v1) pulls the 1-MINUTE tape for the
windows in question and writes verdicts + evidence to fill_audit. It is
research verification of the record — reconstruction-is-not-tape governs
LIVE grading, and by signature this module cannot write the books.
Going forward the question class dies at the source: every new fill
stamps paper_trades.entry_bar with the exact bar(s) its decision read.
"""
import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

log = logging.getLogger("watchtower.fill_audit")

ET = ZoneInfo("America/New_York")
COMPLETE_MARKER = "fill_audit_v1"
AUDIT_DAY = dt.date(2026, 8, 27)


def _mins(bars, t0, t1):
    """1m bars (epoch-ms 'timestamp') within [t0, t1) ET, as dicts."""
    out = []
    for b in bars:
        ts = b.get("timestamp")
        if ts is None:
            continue
        t = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).astimezone(ET)
        if t0 <= t < t1:
            out.append({"et": t.strftime("%H:%M"), "open": b["open"],
                        "high": b["high"], "low": b["low"],
                        "close": b["close"]})
    return out


def audit_level_print(bars_1m, level, entry_et_hm):
    """Pure. Did `level` print BEFORE the entry minute?
    Minutes wholly before the entry minute are conclusive; the entry
    minute itself is sub-minute territory the aggregates can't split.
    Returns (verdict, detail)."""
    before = [b for b in bars_1m if b["et"] < entry_et_hm]
    during = [b for b in bars_1m if b["et"] == entry_et_hm]
    hit_before = [b for b in before if b["high"] >= level]
    if hit_before:
        return ("confirmed",
                f"{level:g} printed at {hit_before[0]['et']} "
                f"(high {hit_before[0]['high']:g}) before the entry minute")
    if during and during[0]["high"] >= level:
        return ("inconclusive_sub_minute",
                f"{level:g} printed within the entry minute "
                f"{entry_et_hm} (high {during[0]['high']:g}) — 1m bars "
                f"cannot order it against the fill second")
    mx = max((b["high"] for b in before + during), default=None)
    return ("refuted",
            f"no print ≥ {level:g} through {entry_et_hm} "
            f"(max high {mx:g})" if mx is not None else "no bars in window")


def audit_close_value(bars_1m, bar_end_et_hm, recorded_px, stored_px):
    """Pure. Which close does the 1m tape support for the 15m bar ending
    at bar_end_et_hm — the fill's value or the stored row's?"""
    prior = [b for b in bars_1m if b["et"] < bar_end_et_hm]
    if not prior:
        return ("inconclusive", "no 1m bars before the bar end")
    last = prior[-1]
    d_rec = abs(last["close"] - recorded_px)
    d_sto = abs(last["close"] - stored_px)
    which = "recorded fill" if d_rec < d_sto else "stored row"
    return ("confirmed" if d_rec < d_sto else "refuted",
            f"final 1m close before {bar_end_et_hm} was {last['close']:g} "
            f"({last['et']}) — nearer the {which} "
            f"(fill {recorded_px:g} Δ{d_rec:.3f} vs stored {stored_px:g} "
            f"Δ{d_sto:.3f})")


def run() -> bool:
    """Fetch the 1m tape once, answer both questions, persist verdicts.
    True when the marker lands (job never re-runs)."""
    from analysis.polygon_data import fetch_recent_bars
    from screen.reversal_screen import _conn

    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True

        bars = fetch_recent_bars("SPY", days=4, multiplier=1,
                                 timespan="minute")
        if not bars:
            log.warning("[fill-audit] no 1m bars returned — retry next boot")
            return False

        d = AUDIT_DAY
        win_a = _mins(bars, dt.datetime(d.year, d.month, d.day, 10, 10, tzinfo=ET),
                      dt.datetime(d.year, d.month, d.day, 10, 31, tzinfo=ET))
        win_b = _mins(bars, dt.datetime(d.year, d.month, d.day, 10, 0, tzinfo=ET),
                      dt.datetime(d.year, d.month, d.day, 10, 16, tzinfo=ET))
        if not win_a or not win_b:
            log.warning("[fill-audit] audit windows empty — vendor history "
                        "not ready; retry next boot")
            return False

        v_a, det_a = audit_level_print(win_a, 770.00, "10:30")
        v_b, det_b = audit_close_value(win_b, "10:15", 768.655, 768.935)

        rows = [
            (87, "SPY",
             "wall_fade_770 entry 10:30:44 — did 770.00 print before the "
             "entry? (_touch 0.1%% tolerance fired on the 769.57 close; "
             "this asks whether the WALL itself traded first)",
             v_a, det_a, json.dumps(win_a)),
            (86, "SPY",
             "flip_hold_768.12 entry 10:15:39 @ 768.655 vs stored touch-bar "
             "close 768.935 — which close does the 1m tape support?",
             v_b, det_b, json.dumps(win_b)),
        ]
        with conn.cursor() as c:
            c.executemany("""INSERT INTO fill_audit
                             (trade_id, ticker, question, verdict, detail,
                              evidence)
                             VALUES (%s,%s,%s,%s,%s,%s::jsonb)""", rows)
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                      "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                      (COMPLETE_MARKER,))
        conn.commit()
        log.info("[fill-audit] trade 87: %s — %s", v_a, det_a)
        log.info("[fill-audit] trade 86: %s — %s", v_b, det_b)
        return True
    finally:
        conn.close()
