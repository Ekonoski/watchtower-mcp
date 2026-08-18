"""
Gamma drift alerts (2026-08-18 — Eric: "no, they change throughout the
day. we have proven that").

The morning gamma board is what gets pasted into the TradingView Tape
Bot slot inputs (CW/PW/GF). The board then re-prices intraday: OI is
fixed overnight, but the max-gamma strike migrates and the flip walks
as spot and vol move — the recorded gex_intraday day-paths show it
(the CPI-day 775→780 wall walk; 2026-08-18's QQQ flip walking
724.72→723.21 with the call wall flapping 730↔700). TradingView cannot
ingest data intraday (Pine sandbox), so the update loop is
Watchtower → Discord → Eric's slot inputs, and this job makes that
loop take fifteen seconds.

Mechanics:
- Baseline = the marks Eric holds. Seeded at 9:20 ET from gex_levels
  (still the morning sweep — the 9:35 intraday upsert would overwrite
  it), advanced to whatever each alert sends. Drift is measured against
  what's in his slots, never against an abstract morning row.
- Rides every 15-minute gex_intraday snapshot (staleness bar 25 min,
  same as the shadow re-armer).
- Material change only: a wall on a DIFFERENT strike, a flip walk
  ≥ 0.30% of spot, or a regime label change. A cent-wobble is one
  level; a wall walk is not (the binary-shadow lesson, reused).
- Rate limit ≥ 40 min between alerts per ticker, ≤ 6/day — a flapping
  wall is measured in the log, not sprayed at a phone.
- EVERY material-change evaluation lands in gamma_drift_alerts, sent or
  suppressed with the reason. The log is the record of how much the
  board actually walks — it decides later whether morning marks
  suffice on pinning days (measured, not argued).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("watchtower.gamma_drift")

# Thresholds — v1, tuned from the alert log once it has a record.
WALL_MIN_PCT = 0.001    # walls this close are the same strike (cent wobble)
FLIP_MIN_PCT = 0.0030   # flip walk that matters, as fraction of spot
STALE_MAX_MIN = 25      # snapshot older than this is a hole, not a reading
ALERT_GAP_MIN = 40      # per-ticker minimum minutes between alerts
ALERT_DAY_CAP = 6       # per-ticker daily ceiling

FIELDS = ("call_wall", "put_wall", "gamma_flip", "regime")


def _f(v):
    return None if v is None else float(v)


def material_changes(marks: dict, snap: dict) -> list:
    """Pure: compare held marks vs a snapshot → list of change dicts.
    A change names the field, both values, and the move; direction is
    never dropped."""
    out = []
    spot = _f(snap.get("spot")) or 0.0
    if spot <= 0:
        return out
    for wall in ("call_wall", "put_wall"):
        old, new = _f(marks.get(wall)), _f(snap.get(wall))
        if old is None or new is None:
            continue
        if abs(new - old) / spot > WALL_MIN_PCT:
            out.append({"field": wall, "old": old, "new": new})
    old, new = _f(marks.get("gamma_flip")), _f(snap.get("gamma_flip"))
    if old is not None and new is not None:
        if abs(new - old) / spot >= FLIP_MIN_PCT:
            out.append({"field": "gamma_flip", "old": old, "new": new})
    if marks.get("regime") and snap.get("regime") \
            and marks["regime"] != snap["regime"]:
        out.append({"field": "regime", "old": marks["regime"],
                    "new": snap["regime"]})
    return out


def rate_limit_reason(last_alert_at, alerts_sent: int, now=None):
    """Pure: None when an alert may send, else the suppression reason."""
    if alerts_sent >= ALERT_DAY_CAP:
        return f"day_cap ({alerts_sent}/{ALERT_DAY_CAP})"
    if last_alert_at is not None:
        now = now or datetime.now(timezone.utc)
        gap = (now - last_alert_at).total_seconds() / 60.0
        if gap < ALERT_GAP_MIN:
            return f"gap ({gap:.0f}m < {ALERT_GAP_MIN}m)"
    return None


def _px(v):
    """Slot-friendly price text: 771.60, not 771.6000."""
    s = f"{float(v):.2f}"
    return s[:-3] if s.endswith(".00") else s


def format_alert(ticker: str, snap_ts_et: str, changes: list,
                 snap: dict) -> str:
    """The message IS the fix: changes stated with direction, then the
    three slot values ready to type."""
    parts = []
    label = {"call_wall": "call wall", "put_wall": "put wall",
             "gamma_flip": "flip", "regime": "REGIME"}
    for c in changes:
        if c["field"] == "regime":
            parts.append(f"REGIME {c['old']} → {c['new']}")
        else:
            parts.append(f"{label[c['field']]} {_px(c['old'])} → {_px(c['new'])}")
    head = f"⚠ **{ticker}** re-marked {snap_ts_et} ET — " + " · ".join(parts)
    slots = (f"Slots: CW {_px(snap['call_wall'])} / PW {_px(snap['put_wall'])}"
             f" / GF {_px(snap['gamma_flip'])}")
    tail = (f"spot {_px(snap['spot'])} · net GEX {float(snap['net_gex']):+.2f}bn"
            f" · {snap['regime']}")
    return f"{head}\n{slots} · {tail}\n(vs the marks last sent; next check ≤15 min)"


def seed_baseline() -> dict:
    """9:20 ET: capture the morning-board marks into gamma_drift_state
    before the 9:35 intraday upsert overwrites gex_levels. These are the
    numbers the morning gamma board printed — the ones in the slots."""
    from analysis.gex import DRIFT_TICKERS
    from screen.reversal_screen import _conn
    conn = _conn()
    seeded = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gamma_drift_state
                    (ticker, trade_date, call_wall, put_wall, gamma_flip,
                     regime, net_gex, source)
                SELECT ticker, as_of, call_wall, put_wall, gamma_flip,
                       regime, net_gex, 'morning_board'
                FROM gex_levels
                WHERE as_of = CURRENT_DATE AND ticker = ANY(%s)
                ON CONFLICT (ticker, trade_date) DO NOTHING
                """,
                (list(DRIFT_TICKERS),),
            )
            seeded = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    log.info(f"[drift] baseline seeded for {seeded} venue(s).")
    return {"seeded": seeded}


def run_gamma_drift_check() -> dict:
    """Ride the 15-minute snapshot: latest gex_intraday row per venue vs
    the held marks; alert material changes (rate-limited), log every
    evaluation, advance the marks to what was sent."""
    from alerts.discord_notify import claim_and_send, is_configured
    from analysis.gex import DRIFT_TICKERS
    from screen.reversal_screen import _conn
    from zoneinfo import ZoneInfo

    if not is_configured("gamma"):
        return {"off": True}

    now = datetime.now(timezone.utc)
    et = ZoneInfo("America/New_York")
    sent = suppressed = quiet = 0
    conn = _conn()
    try:
        for ticker in DRIFT_TICKERS:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ts, spot, net_gex, gamma_flip, call_wall,
                           put_wall, regime
                    FROM gex_intraday
                    WHERE ticker = %s AND ts::date = CURRENT_DATE
                    ORDER BY ts DESC LIMIT 1
                    """,
                    (ticker,),
                )
                row = cur.fetchone()
            if not row:
                continue
            ts, spot, net_gex, flip, cw, pw, regime = row
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts is None or (now - ts) > timedelta(minutes=STALE_MAX_MIN):
                continue  # stale snapshot is a hole, not a reading
            snap = {"spot": spot, "net_gex": net_gex, "gamma_flip": flip,
                    "call_wall": cw, "put_wall": pw, "regime": regime}

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT call_wall, put_wall, gamma_flip, regime,
                           last_alert_at, alerts_sent
                    FROM gamma_drift_state
                    WHERE ticker = %s AND trade_date = CURRENT_DATE
                    """,
                    (ticker,),
                )
                st = cur.fetchone()
            if not st:
                # Deploy mid-day / missing morning row: seed from this
                # snapshot, stated as such — no alert against nothing.
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO gamma_drift_state
                            (ticker, trade_date, call_wall, put_wall,
                             gamma_flip, regime, net_gex, source)
                        VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s,
                                'first_seen')
                        ON CONFLICT (ticker, trade_date) DO NOTHING
                        """,
                        (ticker, cw, pw, flip, regime, net_gex),
                    )
                conn.commit()
                continue

            marks = {"call_wall": st[0], "put_wall": st[1],
                     "gamma_flip": st[2], "regime": st[3]}
            last_alert_at, alerts_sent = st[4], st[5]
            changes = material_changes(marks, snap)
            if not changes:
                quiet += 1
                continue

            reason = rate_limit_reason(last_alert_at, alerts_sent, now)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO gamma_drift_alerts
                        (ticker, trade_date, snapshot_ts, changes,
                         alerted, suppressed_reason)
                    VALUES (%s, CURRENT_DATE, %s, %s::jsonb, %s, %s)
                    """,
                    (ticker, ts, json.dumps(changes), reason is None,
                     reason),
                )
            conn.commit()
            if reason is not None:
                suppressed += 1
                continue

            msg = format_alert(ticker, ts.astimezone(et).strftime("%H:%M"),
                               changes, snap)
            ref = f"{ticker}:{ts.isoformat()}"
            outcome = claim_and_send("gamma_drift", ref, "gamma", msg,
                                     conn=conn)
            if outcome == "sent":
                sent += 1
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE gamma_drift_state
                        SET call_wall=%s, put_wall=%s, gamma_flip=%s,
                            regime=%s, net_gex=%s, source='alert',
                            last_alert_at=now(),
                            alerts_sent=alerts_sent+1, updated_at=now()
                        WHERE ticker=%s AND trade_date=CURRENT_DATE
                        """,
                        (cw, pw, flip, regime, net_gex, ticker),
                    )
                conn.commit()
    finally:
        conn.close()
    return {"sent": sent, "suppressed": suppressed, "quiet": quiet}
