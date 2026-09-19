"""
One-shot backfill of fills_audit for paper_trades written before the
ledger existed.

provenance = backfill_inferred: these rows are reconstructed from the
already-booked trade / spec prices, not from the live fill path. The
script does not compute R or invent a fill price — got_px is the
trade's own entry_px / exit_px / recorded leg price; expected_px is the spec's trigger /
stop / target when those columns exist.

Live rows are left alone (ON CONFLICT DO NOTHING). Reconstruction is
not tape: a backfilled row says so in provenance forever.
"""
import json
import logging

from analysis.fills_audit import INSERT_SQL, PROVENANCES

log = logging.getLogger("watchtower.fills_audit_backfill")

PROVENANCE = "backfill_inferred"
assert PROVENANCE in PROVENANCES


def infer_payloads(row):
    """Pure. row is a mapping with the trade + spec columns listed in
    BACKFILL_SQL. Returns entry, recorded legs, and optional exit tuples.
    Never computes R."""
    tid = row["id"]
    book = row["book"]
    ticker = row["ticker"]
    out = [(
        tid, book, ticker, "entry", row.get("fill_kind"),
        row.get("entry_trigger"), row["entry_px"], False, False,
        None, None, PROVENANCE, 0,
    )]
    if row.get("exited_at") is not None and row.get("exit_px") is not None:
        reason = row.get("exit_reason")
        expected = None
        if reason == "stop":
            expected = row.get("stop")
        elif reason == "target":
            expected = row.get("target")
        out.append((
            tid, book, ticker, "exit",
            "weighted_exit_summary" if row.get("legs") else row.get("fill_kind"),
            expected, row["exit_px"], False, False,
            None, json.dumps({"exit_reason": reason, "legs": row.get("legs") or [],
                              "price_basis": "fraction_weighted_legs" if row.get("legs") else "booked_exit"}),
            PROVENANCE, 0,
        ))
    for index, leg in enumerate(row.get("legs") or [], 1):
        out.append((tid, book, ticker, "leg", leg["why"],
                    None, leg["px"], False, False, None,
                    json.dumps({"fraction": leg["frac"], "reason": leg["why"],
                                "ts": leg["ts"]}), PROVENANCE, index))
    return out


BACKFILL_SQL = """
SELECT t.id, s.book, s.ticker, t.fill_kind, t.entry_px, t.exited_at,
       t.exit_px, t.exit_reason, s.entry_trigger, s.stop, s.target, t.legs
FROM paper_trades t
JOIN paper_specs s ON s.id = t.spec_id
WHERE NOT EXISTS (
    SELECT 1 FROM fills_audit a
    WHERE a.paper_trade_id = t.id AND a.event = 'entry'
)
   OR (t.exited_at IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM fills_audit a
    WHERE a.paper_trade_id = t.id AND a.event = 'exit'
))
   OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(COALESCE(t.legs, '[]'::jsonb))
        WITH ORDINALITY AS leg(value, ordinal)
    WHERE NOT EXISTS (SELECT 1 FROM fills_audit a
        WHERE a.paper_trade_id = t.id AND a.event = 'leg'
          AND a.leg_index = leg.ordinal)
)
ORDER BY s.book, t.id
"""


def run(conn):
    """Insert inferred ledger rows for trades missing them. Returns
    the number of rows inserted. Fail-closed: a failed insert rolls
    the batch back (caller commits)."""
    with conn.cursor() as c:
        c.execute(BACKFILL_SQL)
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]
        payloads = []
        for row in rows:
            for p in infer_payloads(row):
                payloads.append(p)
        n = 0
        for p in payloads:
            c.execute(INSERT_SQL + " ON CONFLICT (paper_trade_id, event, leg_index) "
                      "DO NOTHING", p)
            n += c.rowcount
    return n
