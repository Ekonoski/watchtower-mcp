"""
The walking-target shadow (2026-08-28, Eric: "I just noticed that the
QQQ walls drifted. so I think that is important data").

Live gamma trades freeze stop AND target at entry; the board keeps
re-pricing underneath them. Whether the target should walk with the
wall is measured, not argued: for every resolved gamma trade this
module replays the hold from RECORDED bars (paper_spec_bars) and
RECORDED 15-minute boards (gex_intraday), under two variants:

  walk_both   — target always sits at the wall's current strike
  walk_toward — target may only move TOWARD entry (exits at real force,
                never extends the trade's ambition)

Stops never walk in any variant — a stop that walks away from price is
the account-killer, not a hypothesis. Exits keep live semantics: target
fills on touch, stop on a bar CLOSE through, eod_flat on the last bar.
Promotion: the variant that grades better over ~20-30 shadow-resolved
trades ships as the rule; until then frozen stands. Rides the daily
16:47 pass for new resolutions; a retro seeder backfills history.
Writes only gamma_target_shadow — by signature.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.target_shadow")

RETRO_MARKER = "target_shadow_retro_v1"


def _wall_field(setup: str):
    """Which board level is this trade's target wall?"""
    if setup.startswith("flip_hold"):
        return "call_wall"          # long targets the CW
    if setup.startswith(("wall_fade", "stack_fade")):
        return "gamma_flip"         # short targets the flip (v1: nearer-of
                                    # logic collapses to flip when it was
                                    # chosen; midpoint targets don't walk)
    return None


def walk_exit(direction, entry_px, stop, tgt0, bars, board_targets,
              variant):
    """Pure. Replay one hold with a (possibly) walking target.

    bars: [(end_et, o, c, h, lo)] completed, post-entry, in order.
    board_targets: [(ts, level)] — the wall's recorded path during the
        hold (may be empty → target never moves; that's data, not a bug).
    Returns (exit_px, reason, n_moves) — eod_flat on the last bar if
    nothing else fires."""
    sign = 1 if direction == "long" else -1
    tgt = float(tgt0)
    moves = 0
    bt = sorted(board_targets)
    for i, (end, _o, c, h, lo) in enumerate(bars):
        # Adopt the freshest board level known BEFORE this bar completed.
        level = None
        for ts, lv in bt:
            if ts <= end and lv is not None:
                level = float(lv)
        if level is not None and abs(level - tgt) > 1e-9:
            if variant == "walk_both":
                tgt, moves = level, moves + 1
            elif variant == "walk_toward":
                nearer = (sign > 0 and level < tgt) or (sign < 0 and level > tgt)
                if nearer:
                    tgt, moves = level, moves + 1
        if sign * (c - float(stop)) < 0:
            return c, "stop", moves
        if (direction == "long" and h >= tgt) or \
           (direction == "short" and lo <= tgt):
            return tgt, "target", moves
        if i == len(bars) - 1:
            return c, "eod_flat", moves
    return None, "no_bars", moves


def _shadow_one(conn, tid, ticker, setup, direction, entry_px, stop, tgt,
                entered_at, exited_at, live_r):
    from analysis.paper_trader import ET
    wall = _wall_field(setup)
    if wall is None:
        return
    day = entered_at.astimezone(ET).date()
    with conn.cursor() as c:
        c.execute("""SELECT ts, open, close, high, low FROM paper_spec_bars
                     WHERE ticker=%s AND trade_date=%s AND ts > %s
                     ORDER BY ts""", (ticker, day, entered_at))
        bars = [((r[0].astimezone(ET) + dt.timedelta(minutes=15)),
                 float(r[1]), float(r[2]), float(r[3]), float(r[4]))
                for r in c.fetchall()
                if dt.time(9, 30) <= r[0].astimezone(ET).time() <= dt.time(15, 45)]
        c.execute(f"""SELECT ts, {wall} FROM gex_intraday
                      WHERE ticker=%s AND ts::date=%s ORDER BY ts""",
                  (ticker, day))
        boards = [(r[0], r[1]) for r in c.fetchall()]
    if not bars:
        for v in ("walk_both", "walk_toward"):
            with conn.cursor() as c:
                c.execute("""INSERT INTO gamma_target_shadow
                             (trade_id, variant, note, live_r)
                             VALUES (%s,%s,'hole: no recorded post-entry bars',%s)
                             ON CONFLICT DO NOTHING""", (tid, v, live_r))
        conn.commit()
        return
    for v in ("walk_both", "walk_toward"):
        px, reason, moves = walk_exit(direction, float(entry_px), float(stop),
                                      float(tgt), bars, boards, v)
        risk = abs(float(entry_px) - float(stop)) or 0.01
        sign = 1 if direction == "long" else -1
        r = round(sign * (px - float(entry_px)) / risk, 2) if px else None
        note = None if boards else "no intraday boards — target never walked"
        with conn.cursor() as c:
            c.execute("""INSERT INTO gamma_target_shadow
                         (trade_id, variant, exit_px, exit_reason, r,
                          live_r, n_board_moves, note)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (trade_id, variant) DO NOTHING""",
                      (tid, v, px, reason, r, live_r, moves, note))
    conn.commit()


def run_target_shadow() -> dict:
    """Shadow every resolved gamma trade not yet shadowed (retro + daily
    in one idempotent pass; marker only records that retro completed)."""
    from screen.reversal_screen import _conn
    conn = _conn()
    done = 0
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT t.id, s.ticker, s.setup, s.direction, t.entry_px,
                       s.stop, s.target, t.entered_at, t.exited_at,
                       t.r_multiple
                FROM paper_trades t JOIN paper_specs s ON s.id=t.spec_id
                WHERE s.book LIKE 'gamma%%' AND t.exited_at IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM gamma_target_shadow g
                                  WHERE g.trade_id = t.id)
                ORDER BY t.id""")
            todo = c.fetchall()
        for row in todo:
            _shadow_one(conn, *row)
            done += 1
        with conn.cursor() as c:
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                      "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                      (RETRO_MARKER,))
        conn.commit()
    finally:
        conn.close()
    if done:
        log.info("[target-shadow] shadowed %d resolved gamma trade(s).", done)
    return {"shadowed": done}
