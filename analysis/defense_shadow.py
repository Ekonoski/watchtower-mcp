"""
The defended-entry shadow (2026-08-21 — Eric: "waiting for a smaller
time frame bounce with some increased volume... buyers stepping in and
defending that level").

The hypothesis: on a breakout→retest entry, the blind limit at the
trigger catches falling knives; waiting for visible DEFENSE — red
volume contracting into the touch, then a green bar (or two) closing
back off the level on a volume uptick RELATIVE to the pullback (never
a spike requirement: spikes are late) — skips the knives at the cost
of a premium and some missed V-bottoms. The desk's own record already
leans this way: resolved no-confirm touches average −1.17R (n=4) vs
−0.36R (n=3) for confirmed ones, and the two best positions ever held
(CTNM +4.5R, ASTE +1.5R) were close-confirmed reclaim entries.

Measurement ONLY, per doctrine: the live book keeps its resting limits.
The shadow rides each touch-filled trade — same exit price, same rules,
different entry — and never arms, fills, or cancels anything. Two
variants record side by side so the data picks the definition:
  v1: one green bar closing at/above the trigger with volume > the
      pullback's red-bar average.
  v2: two consecutive green bars with rising volume, the second
      closing at/above the trigger.
Outcomes per touched spec: defended (entry at the confirming close,
premium recorded) · knife_skipped (stop closed through before any
defense — the loss avoided) · missed (ran >0.5% above trigger with no
signature) · no_defense (day ended, neither) · unavailable (bars lack
volume — a hole, never a zero). Promotion gate: ~30 resolved
comparisons, same as every convention on this desk.
"""
import logging

log = logging.getLogger("watchtower.defense_shadow")

MAX_WAIT_BARS = 12       # ~3 hours of 15m bars after the touch
MISS_RUN_PCT = 0.005     # ran half a percent above trigger without defense
PULLBACK_LOOKBACK = 6    # bars before the touch scanned for red baseline


def _is_green(b):
    return b["close"] > b["open"]


def _is_red(b):
    return b["close"] < b["open"]


def find_defense(bars, trigger, stop, touch_idx):
    """Pure detector. bars: list of dicts (ts, open, high, low, close,
    volume — volume may be None) in time order, RTH only. touch_idx:
    index of the bar that touched the trigger. Longs only (the desk's
    only side). Returns {variant: result_dict} for v1 and v2.

    A result names its status and, when defended, the entry (the
    confirming bar's close), the baseline it beat, and the premium.
    Missing volume anywhere it is needed -> 'unavailable' (hole)."""
    pre = bars[max(0, touch_idx - PULLBACK_LOOKBACK):touch_idx + 1]
    reds = [b for b in pre if _is_red(b)]
    base_pool = reds if len(reds) >= 2 else pre
    if any(b.get("volume") is None for b in base_pool) or not base_pool:
        return {v: {"status": "unavailable"} for v in ("v1", "v2")}
    base_vol = sum(float(b["volume"]) for b in base_pool) / len(base_pool)

    out = {}
    for variant in ("v1", "v2"):
        res = {"status": "no_defense", "base_vol": base_vol}
        prev = None
        for b in bars[touch_idx + 1: touch_idx + 1 + MAX_WAIT_BARS]:
            if b.get("volume") is None:
                res = {"status": "unavailable"}
                break
            # Knife first: a close through the stop before defense means
            # the blind fill is riding a loser the shadow never took.
            if b["close"] <= stop:
                res = {"status": "knife_skipped", "base_vol": base_vol}
                break
            defended = False
            if variant == "v1":
                defended = (_is_green(b) and b["close"] >= trigger
                            and float(b["volume"]) > base_vol)
            else:
                defended = (prev is not None and _is_green(prev)
                            and _is_green(b)
                            and float(b["volume"]) > float(prev["volume"])
                            and b["close"] >= trigger)
            if defended:
                res = {"status": "defended", "px": b["close"],
                       "at": b["ts"], "base_vol": base_vol,
                       "defense_vol": float(b["volume"]),
                       "premium_pct": (b["close"] - trigger) / trigger}
                break
            if b["close"] >= trigger * (1 + MISS_RUN_PCT):
                res = {"status": "missed", "base_vol": base_vol}
                break
            prev = b
        out[variant] = res
    return out


def _bars_for(conn, ticker, trade_date):
    """Recorded RTH bars with volume for the shadow — live grading reads
    stored tape only (reconstruction is not tape)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts, open, high, low, close, volume FROM paper_spec_bars
            WHERE ticker = %s AND trade_date = %s
              AND (ts AT TIME ZONE 'America/New_York')::time
                  BETWEEN '09:30' AND '15:45'
            ORDER BY ts
            """,
            (ticker, trade_date),
        )
        return [{"ts": r[0], "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]),
                 "volume": float(r[5]) if r[5] is not None else None}
                for r in cur.fetchall()]


def evaluate_defense_shadows() -> dict:
    """Poll: (1) create shadow rows for today's touch fills lacking
    them, from recorded bars; (2) fill shadow_r/live_r on rows whose
    live trade has exited. Never writes to paper_trades or paper_specs."""
    from screen.reversal_screen import _conn
    conn = _conn()
    created = graded = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, s.ticker, s.entry_trigger, s.stop,
                       t.entered_at, t.entered_at::date
                FROM paper_trades t JOIN paper_specs s ON s.id = t.spec_id
                WHERE s.book = 'swing' AND t.fill_kind = 'touch'
                  AND t.entered_at::date = CURRENT_DATE
                  AND NOT EXISTS (SELECT 1 FROM paper_defense_shadow d
                                  WHERE d.trade_id = t.id)
                """
            )
            todo = cur.fetchall()
        for tid, tk, trig, stop, entered_at, tdate in todo:
            trig, stop = float(trig), float(stop)
            bars = _bars_for(conn, tk, tdate)
            # The touch bar is the FIRST bar whose low reached the
            # trigger — a resting limit fills the moment the first
            # touch prints, whatever the trade row's timestamp says
            # (2026-08-24: outage-recovery fills carried hours-late
            # timestamps; the fill-time window pointed at the wrong
            # bar). Fall back to the fill-time window only if no bar
            # shows the touch (a recorded-tape hole).
            import datetime as _dt
            touch_idx = next((i for i, b in enumerate(bars)
                              if b["low"] <= trig), None)
            if touch_idx is None:
                for i, b in enumerate(bars):
                    if b["ts"] <= entered_at < b["ts"] + _dt.timedelta(minutes=15):
                        touch_idx = i
                        break
            if touch_idx is None:
                continue  # touch bar not recorded yet; next pass
            results = find_defense(bars, trig, stop, touch_idx)
            with conn.cursor() as cur:
                for variant, r in results.items():
                    cur.execute(
                        """
                        INSERT INTO paper_defense_shadow
                            (trade_id, variant, status, defense_px,
                             defense_at, base_vol, defense_vol, premium_pct)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (trade_id, variant) DO NOTHING
                        """,
                        (tid, variant, r["status"], r.get("px"),
                         r.get("at"), r.get("base_vol"),
                         r.get("defense_vol"), r.get("premium_pct")),
                    )
            conn.commit()
            created += 1

        # Grade: live trade exited -> shadow rides the same exit.
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.status, d.defense_px, t.exit_px,
                       t.r_multiple, s.stop
                FROM paper_defense_shadow d
                JOIN paper_trades t ON t.id = d.trade_id
                JOIN paper_specs s ON s.id = t.spec_id
                WHERE d.shadow_r IS NULL AND t.exited_at IS NOT NULL
                """
            )
            rows = cur.fetchall()
        with conn.cursor() as cur:
            for did, status, dpx, exit_px, live_r, stop in rows:
                if status == "defended" and dpx is not None \
                        and exit_px is not None:
                    risk = float(dpx) - float(stop)
                    sr = ((float(exit_px) - float(dpx)) / risk
                          if risk > 0 else None)
                elif status in ("knife_skipped", "missed", "no_defense"):
                    sr = 0.0   # the shadow desk never took the trade
                else:
                    sr = None  # unavailable stays a hole
                cur.execute(
                    "UPDATE paper_defense_shadow SET shadow_r=%s, "
                    "live_r=%s, updated_at=now() WHERE id=%s",
                    (sr, live_r, did),
                )
                graded += 1
        conn.commit()
    finally:
        conn.close()
    if created or graded:
        log.info(f"[defense] shadows created={created} graded={graded}")
    return {"created": created, "graded": graded}
