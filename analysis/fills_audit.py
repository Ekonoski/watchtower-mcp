"""
fills_audit — the live fill-provenance ledger (plural).

Paper trades used to write with almost no fill evidence. The existing
`fill_audit` table (singular) is the 2026-08-27 forensic Q&A on two
questioned SPY fills — do not overload it. This module writes one
entry row, execution-leg rows and one exit summary row into `fills_audit`,
on the SAME cursor / transaction as the paper_trades mutation. If the audit
insert fails, the caller must not commit: no silent paper without a
ledger row.

got_px is the price that printed (or the booked fill). expected_px is
the level the spec named (trigger / stop / target) when one exists.
This module does not compute R or P&L.

gap_report is report-only and counts BY BOOK. Pooling books into one
gap score is forbidden — a quiet gamma day must not hide a swing hole.
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("watchtower.fills_audit")

EVENTS = ("entry", "exit", "leg")
PROVENANCES = ("live", "backfill_inferred")

INSERT_SQL = """INSERT INTO fills_audit
    (paper_trade_id, book, ticker, event, fill_kind,
     expected_px, got_px, gap_through, phantom_stop,
     bar, evidence, provenance, leg_index)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)"""


def _require(name, val):
    if val is None or val == "":
        raise ValueError(f"fills_audit {name} is required")
    return val


def _jsonb(val):
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return json.dumps(val, default=str)


def _insert(cur, *, paper_trade_id, book, ticker, event, got_px,
            fill_kind=None, expected_px=None, gap_through=False,
            phantom_stop=False, bar=None, evidence=None,
            provenance="live", leg_index=0):
    """Write one ledger row on `cur`. Raises on any bad field or
    execute failure so the surrounding trade transaction cannot
    commit without the audit."""
    _require("paper_trade_id", paper_trade_id)
    _require("book", book)
    _require("ticker", ticker)
    _require("got_px", got_px)
    if event not in EVENTS:
        raise ValueError(f"fills_audit event must be {EVENTS}, got {event!r}")
    if provenance not in PROVENANCES:
        raise ValueError(
            f"fills_audit provenance must be {PROVENANCES}, got {provenance!r}")
    if (event == "leg" and leg_index < 1) or (event != "leg" and leg_index != 0):
        raise ValueError("leg_index must be positive for legs and zero otherwise")
    cur.execute(INSERT_SQL, (
        paper_trade_id, book, ticker, event, fill_kind,
        expected_px, got_px, bool(gap_through), bool(phantom_stop),
        _jsonb(bar), _jsonb(evidence), provenance, leg_index,
    ))


def record_entry(cur, paper_trade_id, book, ticker, got_px,
                 fill_kind=None, expected_px=None, gap_through=False,
                 bar=None, evidence=None, provenance="live"):
    """Entry ledger row. Call on the same cursor as the paper_trades
    INSERT, before commit."""
    _insert(cur, paper_trade_id=paper_trade_id, book=book, ticker=ticker,
            event="entry", got_px=got_px, fill_kind=fill_kind,
            expected_px=expected_px, gap_through=gap_through,
            phantom_stop=False, bar=bar, evidence=evidence,
            provenance=provenance)


def record_exit(cur, paper_trade_id, book, ticker, got_px,
                fill_kind=None, expected_px=None, gap_through=False,
                phantom_stop=False, bar=None, evidence=None,
                provenance="live"):
    """Exit ledger row. Call on the same cursor as the paper_trades
    UPDATE that sets exited_at / exit_px, before commit."""
    _insert(cur, paper_trade_id=paper_trade_id, book=book, ticker=ticker,
            event="exit", got_px=got_px, fill_kind=fill_kind,
            expected_px=expected_px, gap_through=gap_through,
            phantom_stop=phantom_stop, bar=bar, evidence=evidence,
            provenance=provenance)


def ensure_schema(conn):
    """Apply the checked-in, idempotent migration before starting jobs.

    This owns a dedicated boot connection; never call inside a trade txn.
    Failure propagates so the service cannot report a healthy scheduler
    with a missing ledger. No grants, RLS changes or historical backfill.
    """
    ddl = (Path(__file__).resolve().parents[1] /
           "migrations/076_fills_audit.sql").read_text()
    try:
        with conn.cursor() as cur:
            # Serialize simultaneous worker starts, including first CREATE.
            cur.execute("SELECT pg_advisory_xact_lock(760076)")
            cur.execute(ddl)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.info("[fills-audit] schema ready (entry, execution legs, exit summary)")


def record_legs(cur, paper_trade_id, book, ticker, legs, previous, bars, stop):
    """Append newly booked executions, keyed by their 1-based leg ordinal.

    The caller locks the trade before reading previous legs. Old legs stay
    historical holes until explicitly inferred by the optional backfill;
    replaying them now must not relabel them as live evidence.
    """
    previous = previous or []
    if legs[:len(previous)] != previous:
        raise ValueError("recorded execution legs changed; refusing to rewrite provenance")
    by_ts = {b[0].isoformat(): b for b in bars}
    for index, leg in enumerate(legs[len(previous):], len(previous) + 1):
        b = by_ts.get(leg["ts"])
        if b is None:
            raise ValueError("execution leg has no decision bar")
        expected = stop if leg["why"] == "stop" else (
            None if leg["why"] == "bell" else leg["px"])
        _insert(cur, paper_trade_id=paper_trade_id, book=book, ticker=ticker,
                event="leg", leg_index=index, got_px=leg["px"],
                expected_px=expected, fill_kind=leg["why"],
                bar={"ts": leg["ts"], "open": b[1], "high": b[2],
                     "low": b[3], "close": b[4]},
                evidence={"fraction": leg["frac"], "reason": leg["why"]})


def required_events(exited_at):
    """Required trade-level events (execution-leg parity is checked separately).
    An open trade needs an entry row; a closed trade needs both.
    Zero is data: a still-open trade is not missing its exit."""
    return ("entry",) if exited_at is None else ("entry", "exit")


def missing_events(exited_at, events):
    have = set(events or ())
    return [e for e in required_events(exited_at) if e not in have]


def gap_report(trades, audits):
    """Report-only. Count ledger holes BY BOOK.

    trades: iterable of {id, book, exited_at, legs} (legs defaults empty)
    audits: iterable of {paper_trade_id, event, leg_index}

    Returns {book: {n_trades, n_open, n_closed, missing_entry,
                    missing_exit, missing_legs, complete}}. No pooled score — a
    caller that sums books into one gap rate is inventing a number
    this function refuses to print.
    """
    by_trade, leg_indices = {}, {}
    for a in audits:
        by_trade.setdefault(a["paper_trade_id"], set()).add(a["event"])
        if a["event"] == "leg":
            leg_indices.setdefault(a["paper_trade_id"], set()).add(a.get("leg_index"))
    out = {}
    for t in trades:
        book = t["book"]
        slot = out.setdefault(book, {
            "n_trades": 0, "n_open": 0, "n_closed": 0,
            "missing_entry": 0, "missing_exit": 0, "missing_legs": 0, "complete": 0,
        })
        slot["n_trades"] += 1
        closed = t.get("exited_at") is not None
        slot["n_closed" if closed else "n_open"] += 1
        miss = missing_events(t.get("exited_at"),
                              by_trade.get(t["id"], ()))
        if "entry" in miss:
            slot["missing_entry"] += 1
        if "exit" in miss:
            slot["missing_exit"] += 1
        required_legs = set(range(1, len(t.get("legs") or []) + 1))
        legs_missing = required_legs - leg_indices.get(t["id"], set())
        slot["missing_legs"] += len(legs_missing)
        if not miss and not legs_missing:
            slot["complete"] += 1
    return out
