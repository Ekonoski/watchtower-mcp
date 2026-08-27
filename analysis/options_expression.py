"""
The swing options-expression shadow (2026-08-27, Eric: "we also need to
be swing trading options as well... let's definitely build this").

The names are NOT chosen here — the swing book's graded classes choose
the names; this layer only asks which of those signals options can
EXPRESS without destroying the edge, and measures the answer. For every
swing fill it builds the ticket the desk would buy — an ITM call
(~0.70 delta), tenor by class (weekly signals 55-100 DTE, daily 28-50)
— priced from the live chain at entry, and priced again when the live
trade exits. The option's round trip rides beside the share round trip
in the record; ~30 resolved comparisons decide whether the wrapper
amplifies or eats the edge, per Eric's 2026-08-10 gate (the live
options paper book opens only after the swing book proves out).

Honesty rules, house standard:
  - A signal options can't express records WHY (illiquid / no_chain /
    no_mark) — a silent filter is a _social_block.
  - Marks are the chain's own prints (day close / last trade). The
    snapshot carries no bid/ask here, so spread cost is a DECLARED HOLE
    in v1, not a zero — stated wherever the comparison renders.
  - Entry tickets are built same-day only; a missed window is a 'hole',
    never a reconstructed price (reconstruction is not tape).
  - This module reads the books and writes only its own table — pinned
    by signature in tests/test_options_expression.py.
"""
import datetime as dt
import json
import logging

log = logging.getLogger("watchtower.options_expression")

TARGET_DELTA = 0.70
MIN_OI = 100
STRIKE_LO_X, STRIKE_HI_X = 0.70, 1.02   # ITM call band around share entry
WEEKLY_DTE = (55, 100)
DAILY_DTE = (28, 50)
EXIT_STALE_DAYS = 2   # exit marks older than this are holes, not prices


def dte_window(setup: str):
    """Tenor by class: weekly-timeframe signals get the longer wrapper."""
    return WEEKLY_DTE if setup and setup.endswith("_weekly") else DAILY_DTE


def pick_contract(rows: list, spot: float):
    """Pure. Choose the expression contract from chain rows (dicts with
    strike/exp/delta/iv/oi/last/occ) and say why when we can't.
    Returns (row_or_None, verdict, note)."""
    if not rows:
        return None, "no_chain", "no contracts in the DTE/strike window"
    with_delta = [r for r in rows if r.get("delta")]
    if with_delta:
        pick = min(with_delta, key=lambda r: abs(float(r["delta"]) - TARGET_DELTA))
    else:
        pick = min(rows, key=lambda r: abs(float(r["strike"]) - 0.85 * spot))
    if not pick.get("oi") or int(pick["oi"]) < MIN_OI:
        return pick, "illiquid", (f"best contract OI "
                                  f"{pick.get('oi') or 0} < {MIN_OI}")
    if not pick.get("last"):
        return pick, "no_mark", "no traded price on the chain snapshot"
    return pick, "ticket", None


def run_options_expression() -> dict:
    """Two passes, both idempotent:
    (1) entry tickets for today's swing fills that lack one;
    (2) exit marks for ticketed trades whose live trade has exited."""
    from analysis.options_picker import _earnings_inside, _fetch_chain, iv_rank
    from analysis.paper_trader import ET
    from screen.reversal_screen import _conn

    today = dt.datetime.now(ET).date()
    conn = _conn()
    made = priced = holes = 0
    try:
        # ---- entry pass ---------------------------------------------------
        with conn.cursor() as c:
            c.execute("""
                SELECT t.id, s.ticker, s.setup, t.entry_px,
                       (t.entered_at AT TIME ZONE 'America/New_York')::date
                FROM paper_trades t JOIN paper_specs s ON s.id = t.spec_id
                WHERE s.book = 'swing'
                  AND NOT EXISTS (SELECT 1 FROM options_expression o
                                  WHERE o.trade_id = t.id)
                ORDER BY t.id""")
            todo = c.fetchall()
        for tid, tk, setup, entry_px, entered_d in todo:
            entry_px = float(entry_px)
            if entered_d != today:
                # Missed the live window — a hole, never a reconstruction.
                with conn.cursor() as c:
                    c.execute("""INSERT INTO options_expression
                        (trade_id, ticker, setup, verdict, note, entry_spot)
                        VALUES (%s,%s,%s,'hole',
                                'entry predates the shadow / missed same-day window',
                                %s)
                        ON CONFLICT (trade_id) DO NOTHING""",
                              (tid, tk, setup, entry_px))
                conn.commit()
                holes += 1
                continue
            lo_d, hi_d = dte_window(setup)
            rows = _fetch_chain(tk, "call",
                                today + dt.timedelta(days=lo_d),
                                today + dt.timedelta(days=hi_d),
                                entry_px * STRIKE_LO_X,
                                entry_px * STRIKE_HI_X)
            pick, verdict, note = pick_contract(rows, entry_px)
            exp = dte = None
            if pick is not None and pick.get("exp"):
                try:
                    exp = dt.date.fromisoformat(pick["exp"])
                    dte = (exp - today).days
                except ValueError:
                    exp = None
            ivr = None
            try:
                r = iv_rank(conn, tk)   # None until ~20 own snapshots exist
                ivr = r.get("iv_rank") if isinstance(r, dict) else None
            except Exception:
                pass
            earn = _earnings_inside(conn, tk, exp) if exp else None
            with conn.cursor() as c:
                c.execute("""INSERT INTO options_expression
                    (trade_id, ticker, setup, verdict, note, occ, expiry,
                     strike, dte, delta, iv, iv_rank, oi, entry_mark,
                     entry_spot, earnings)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s::jsonb)
                    ON CONFLICT (trade_id) DO NOTHING""",
                          (tid, tk, setup, verdict, note,
                           pick.get("occ") if pick else None, exp,
                           pick.get("strike") if pick else None, dte,
                           pick.get("delta") if pick else None,
                           pick.get("iv") if pick else None, ivr,
                           pick.get("oi") if pick else None,
                           pick.get("last") if pick else None,
                           entry_px,
                           json.dumps(earn) if earn else None))
            conn.commit()
            made += 1
            log.info("[opt-expr] trade %s %s → %s%s", tid, tk, verdict,
                     f" {pick['occ']} @ {pick['last']}" if verdict == "ticket"
                     else f" ({note})")

        # ---- exit pass ----------------------------------------------------
        with conn.cursor() as c:
            c.execute("""
                SELECT o.id, o.trade_id, o.ticker, o.expiry, o.strike,
                       t.exit_px,
                       (t.exited_at AT TIME ZONE 'America/New_York')::date
                FROM options_expression o
                JOIN paper_trades t ON t.id = o.trade_id
                WHERE o.verdict = 'ticket' AND o.exit_mark IS NULL
                  AND t.exited_at IS NOT NULL""")
            exits = c.fetchall()
        for oid, tid, tk, exp, strike, exit_px, exited_d in exits:
            if (today - exited_d).days > EXIT_STALE_DAYS:
                with conn.cursor() as c:
                    c.execute("""UPDATE options_expression
                                 SET verdict='hole',
                                     note='exit window missed — no honest mark'
                                 WHERE id=%s""", (oid,))
                conn.commit()
                holes += 1
                continue
            strike = float(strike)
            rows = _fetch_chain(tk, "call", exp, exp,
                                strike - 0.01, strike + 0.01)
            mark = rows[0].get("last") if rows else None
            if mark is None:
                continue          # retry next pass; stale-window rule caps it
            with conn.cursor() as c:
                c.execute("""UPDATE options_expression
                             SET exit_mark=%s, exit_spot=%s,
                                 exit_priced_at=now()
                             WHERE id=%s""",
                          (mark, exit_px, oid))
            conn.commit()
            priced += 1
            log.info("[opt-expr] trade %s %s exit mark %.2f", tid, tk, mark)
    finally:
        conn.close()
    return {"tickets": made, "exits_priced": priced, "holes": holes}
