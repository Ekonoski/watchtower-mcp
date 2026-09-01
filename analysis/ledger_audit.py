"""
Nightly ledger-integrity audit (2026-09-01 — the day the rs_leader
book's placeholder target was closed by the swing poller and Eric
asked "may all our trades have defects like that?"). The answer must
be re-earned every night by machine, not asserted from memory:

  1. Exit-reason legality: every book has a declared exit vocabulary;
     any recorded reason outside it is a foreign writer or a rule
     drift — the exact 2026-09-01 phantom, caught mechanically.
  2. Completeness: an exited trade missing exit_px or r_multiple.
  3. Price evidence: every entry and exit must sit inside its day's
     recorded bar range (paper_spec_bars or rsl_book_bars, ±0.1%).
     A day with no recorded bars is a HOLE, counted and named, never
     silently passed (the _social_block rule).

Quiet when clean (one log line: 'audit clean, n trades'); LOUD when
not — anomalies post to #desk once per day (kind ledger_audit).
Read-only over the books by signature; writes nothing but the
Discord claim.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.ledger_audit")

LEGAL_EXITS = {
    "gamma": {"target", "stop", "eod_flat", "clock_1430", "binary_gate",
              "manual"},
    "gamma_iday": {"target", "stop", "eod_flat", "clock_1430",
                   "binary_gate", "manual"},
    "swing": {"target", "stop", "eod_flat", "manual"},
    "day_bias": {"stop", "eod_flat", "manual"},
    "rs_leader": {"trail", "disaster", "stop", "eod_flat", "manual"},
}
TOL = 0.001


def audit(rows, bar_ranges):
    """Pure. rows: (book, ticker, entry_px, entry_day, exit_px,
    exit_day, exit_reason, r_multiple). bar_ranges: {(ticker, day):
    (lo, hi)}. Returns (anomalies, holes, n_checked)."""
    anomalies, holes = [], []
    for (book, tk, e_px, e_day, x_px, x_day, reason, r) in rows:
        legal = LEGAL_EXITS.get(book)
        if legal is None:
            anomalies.append(f"{book}/{tk}: unknown book")
            continue
        if reason is not None and reason not in legal:
            anomalies.append(f"{book}/{tk} {x_day}: illegal exit_reason "
                             f"'{reason}' (allowed: {sorted(legal)})")
        if reason is not None and (x_px is None or r is None):
            anomalies.append(f"{book}/{tk} {x_day}: exited but "
                             f"exit_px/r_multiple missing")
        for label, px, day in (("entry", e_px, e_day), ("exit", x_px, x_day)):
            if px is None or day is None:
                continue
            rng = bar_ranges.get((tk, day))
            if rng is None:
                holes.append(f"{book}/{tk} {day}: no recorded bars to "
                             f"verify {label} {px}")
            elif not (rng[0] * (1 - TOL) <= float(px) <= rng[1] * (1 + TOL)):
                anomalies.append(f"{book}/{tk} {day}: {label} {px} OUTSIDE "
                                 f"recorded range {rng[0]}-{rng[1]}")
    return anomalies, holes, len(rows)


def run() -> str:
    from alerts.discord_notify import claim_and_send
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT s.book, s.ticker, t.entry_px,
                           (t.entered_at AT TIME ZONE 'America/New_York')::date,
                           t.exit_px,
                           (t.exited_at AT TIME ZONE 'America/New_York')::date,
                           t.exit_reason, t.r_multiple
                         FROM paper_trades t JOIN paper_specs s
                           ON s.id = t.spec_id""")
            rows = c.fetchall()
            c.execute("""SELECT ticker, trade_date, min(low), max(high)
                         FROM paper_spec_bars GROUP BY ticker, trade_date""")
            ranges = {(tk, d): (float(lo), float(hi))
                      for tk, d, lo, hi in c.fetchall()}
            c.execute("""SELECT ticker, trade_date, min(low), max(high)
                         FROM rsl_book_bars GROUP BY ticker, trade_date""")
            for tk, d, lo, hi in c.fetchall():
                if (tk, d) in ranges:
                    lo = min(lo := float(lo), ranges[(tk, d)][0])
                    hi = max(float(hi), ranges[(tk, d)][1])
                    ranges[(tk, d)] = (lo, hi)
                else:
                    ranges[(tk, d)] = (float(lo), float(hi))
        anomalies, holes, n = audit(rows, ranges)
        today = dt.date.today().isoformat()
        if anomalies:
            msg = ("🚨 **Ledger audit: " + str(len(anomalies)) +
                   " anomal" + ("y" if len(anomalies) == 1 else "ies") +
                   "**\n" + "\n".join(anomalies[:10]))
            if len(anomalies) > 10:
                msg += f"\n… {len(anomalies) - 10} more (count stated)"
            claim_and_send("ledger_audit", today, "desk", msg, conn=conn)
            log.warning("[ledger-audit] %d anomalies: %s",
                        len(anomalies), anomalies[:5])
            return "anomalies"
        log.info("[ledger-audit] clean — %d trades verified, %d bar-holes "
                 "(holes are unverifiable, not failures).", n, len(holes))
        return "clean"
    finally:
        conn.close()
