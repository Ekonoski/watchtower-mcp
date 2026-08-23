"""
Gamma proximity alerts (2026-08-23 — Eric: "tell me when a ticker is
trading at or near those levels so I know to go and watch them").

The drift pipe already re-prices every venue's board each 15 minutes;
this rides the same snapshots and pings the SAME #gamma-drift channel
when spot ENTERS the neighborhood of a wall or the flip. It is a
watch prompt, never a signal — the message carries the reading rules:

- MAGNITUDE RULE on every line: net GEX printed always; below
  DECOR_BN the alert says so — "decoration magnitude: trade the
  chart, not the wall." Dust boards (|GEX| < MIN_GEX_BN) never alert.
- INVERTED WALLS labeled (the NVDA lesson): a put wall ABOVE spot is
  stranded protection overhead, not support; a call wall BELOW spot
  is a stabilizer underneath, not resistance.
- One ping per (day, ticker, level-kind, strike): in a pinning regime
  price SITS on the magnet — re-pinging every 15 minutes would bury
  the phone. A wall that WALKS to a new strike is a new level and may
  ping again; a cent wobble is the same level (quantized ref, the
  binary-shadow lesson reused). At-most-once via discord_notify_log
  claims, same as every other alert kind.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger("watchtower.gamma_prox")

PROX_PCT = float(os.environ.get("GAMMA_PROX_PCT", "0.0025"))  # 0.25% band
MIN_GEX_BN = 0.05     # dust boards never alert
DECOR_BN = 0.5        # below this, the wall is decoration and says so
STALE_MAX_MIN = 25    # same staleness bar as drift / the shadow re-armer

_KINDS = (("call_wall", "CALL WALL"), ("put_wall", "PUT WALL"),
          ("gamma_flip", "FLIP"))


def near_levels(spot, cw, pw, flip, band: float = PROX_PCT) -> list:
    """Pure: which levels is spot within `band` of? Returns
    [{kind, label, level, dist_pct}] — dist signed, spot above level
    positive. Null levels are skipped, never invented."""
    out = []
    if not spot or float(spot) <= 0:
        return out
    spot = float(spot)
    for (kind, label), lv in zip(_KINDS, (cw, pw, flip)):
        if lv is None or float(lv) <= 0:
            continue
        lv = float(lv)
        d = (spot - lv) / lv
        if abs(d) <= band:
            out.append({"kind": kind, "label": label, "level": lv,
                        "dist_pct": round(d * 100, 2)})
    return out


def prox_ref(trade_date, ticker: str, kind: str, level) -> str:
    """Claim key: one ping per day per ticker per level-kind per STRIKE.
    Quantized to cents so a wobble is the same level and a walk to a
    new strike is a new one."""
    return f"{trade_date}:{ticker}:{kind}:{float(level):.2f}"


def format_prox(ticker: str, ts_et: str, spot: float, hit: dict,
                net_gex, regime) -> str:
    spot, lv = float(spot), hit["level"]
    side = "above" if hit["dist_pct"] >= 0 else "below"
    inv = ""
    if hit["kind"] == "put_wall" and lv > spot:
        inv = " · INVERTED: stranded protection overhead — congestion, not support"
    elif hit["kind"] == "call_wall" and lv < spot:
        inv = " · INVERTED: stabilizer underneath, not resistance"
    elif hit["kind"] == "gamma_flip":
        inv = " · pinning above / slippery below"
    gex = float(net_gex) if net_gex is not None else None
    gex_txt = f"net {gex:+.2f}bn" if gex is not None else "net GEX n/a"
    decor = (" · ⚠ decoration magnitude — trade the chart, not the wall"
             if gex is not None and abs(gex) < DECOR_BN else "")
    return (f"📍 **{ticker}** at {hit['label']} {lv:g} — spot {spot:g} "
            f"({hit['dist_pct']:+.2f}% {side}) · {gex_txt} · "
            f"{regime or '?'}{inv}{decor}\n"
            f"({ts_et} ET board · watch prompt, not a signal · "
            f"one ping per level per day)")


def run_gamma_prox_check() -> dict:
    """Ride the 15-minute snapshot: freshest board per venue; ping the
    gamma channel when spot sits inside the band of a level it hasn't
    pinged today. Reads the record, writes nothing but the notify log."""
    from zoneinfo import ZoneInfo

    from alerts.discord_notify import (POST_SPACING_S, claim_and_send,
                                       is_configured)
    from analysis.gex import DRIFT_TICKERS
    from screen.reversal_screen import _conn
    import time as _time

    if not is_configured("gamma"):
        return {"off": True}
    now = datetime.now(timezone.utc)
    et = ZoneInfo("America/New_York")
    sent = skipped = 0
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
                continue   # stale board is a hole, not a reading
            if net_gex is not None and abs(float(net_gex)) < MIN_GEX_BN:
                continue   # dust board — never present it as levels
            for hit in near_levels(spot, cw, pw, flip):
                ref = prox_ref(ts.astimezone(et).date(), ticker,
                               hit["kind"], hit["level"])
                msg = format_prox(ticker, ts.astimezone(et).strftime("%H:%M"),
                                  spot, hit, net_gex, regime)
                res = claim_and_send("gamma_prox", ref, "gamma", msg, conn)
                if res == "sent":
                    sent += 1
                    _time.sleep(POST_SPACING_S)
                else:
                    skipped += 1
    finally:
        conn.close()
    if sent:
        log.info(f"[gamma-prox] {sent} proximity ping(s), {skipped} held.")
    return {"sent": sent, "skipped": skipped}
