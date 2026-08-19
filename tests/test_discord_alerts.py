"""Discord notification pipe + gamma drift alerts (2026-08-18).

What this file pins:
  1. Drift materiality: a wall landing on a different strike fires; a
     cent-wobble does not (the binary-shadow lesson — the flip's
     cent-wobble is one level, a 775→780 wall walk is not). The flip
     threshold is 0.30% of spot; a regime label change always fires.
  2. Rate limiting is a suppression with a REASON, never a silent drop:
     the day cap and the 40-minute gap each name themselves.
  3. The alert message carries the fix, not just the fact: slot values
     (CW / PW / GF) ready to type into the Tape Bot inputs, direction
     kept on every change.
  4. Configured-off is a clean no-op: with no webhook env, post_discord
     returns False without attempting the network and claim_and_send
     returns 'off' without touching the database.
  5. Desk event text: exits carry realized R signed, fills carry the
     trigger when entry differs from it (the reclaim premium stays
     visible).
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure configured-off before importing the module under test.
for _v in ("DISCORD_WEBHOOK_GAMMA", "DISCORD_WEBHOOK_DESK",
           "DISCORD_WEBHOOK_URL"):
    os.environ.pop(_v, None)

from alerts.discord_notify import claim_and_send, is_configured, post_discord  # noqa: E402
from alerts.gamma_drift import (  # noqa: E402
    ALERT_DAY_CAP, format_alert, material_changes, rate_limit_reason)
from alerts.desk_events import format_exit, format_fill  # noqa: E402

UTC = dt.timezone.utc


# The 2026-08-18 QQQ tape as the fixture: morning marks vs the 11:15
# snapshot family.
MARKS = {"call_wall": 735.0, "put_wall": 720.0, "gamma_flip": 724.72,
         "regime": "slippery"}


def test_wall_walk_fires_cent_wobble_does_not():
    # Call wall migrated 735 -> 730: a different strike, material.
    snap = {"spot": 717.9, "call_wall": 730.0, "put_wall": 720.0,
            "gamma_flip": 724.72, "regime": "slippery"}
    fields = [c["field"] for c in material_changes(MARKS, snap)]
    assert fields == ["call_wall"], fields

    # Cent-wobble on the put wall (720 -> 720.05): same level, silent.
    snap = {"spot": 717.9, "call_wall": 735.0, "put_wall": 720.05,
            "gamma_flip": 724.72, "regime": "slippery"}
    assert material_changes(MARKS, snap) == []


def test_flip_walk_threshold_is_030_pct_of_spot():
    # 724.72 -> 723.21 on ~718 spot is 0.21% — below the bar, silent.
    snap = {"spot": 717.9, "call_wall": 735.0, "put_wall": 720.0,
            "gamma_flip": 723.21, "regime": "slippery"}
    assert material_changes(MARKS, snap) == []

    # 724.72 -> 722.30 is 0.34% — fires, direction preserved.
    snap = {"spot": 717.9, "call_wall": 735.0, "put_wall": 720.0,
            "gamma_flip": 722.30, "regime": "slippery"}
    ch = material_changes(MARKS, snap)
    assert [c["field"] for c in ch] == ["gamma_flip"]
    assert ch[0]["old"] == 724.72 and ch[0]["new"] == 722.30


def test_regime_change_always_fires():
    snap = {"spot": 726.0, "call_wall": 735.0, "put_wall": 720.0,
            "gamma_flip": 724.72, "regime": "pinning"}
    ch = material_changes(MARKS, snap)
    assert [c["field"] for c in ch] == ["regime"]
    assert ch[0]["old"] == "slippery" and ch[0]["new"] == "pinning"


def test_missing_fields_are_holes_not_zero_moves():
    snap = {"spot": 717.9, "call_wall": None, "put_wall": 720.0,
            "gamma_flip": None, "regime": "slippery"}
    assert material_changes(MARKS, snap) == []
    assert material_changes(MARKS, {"spot": 0}) == []


def test_rate_limit_names_its_reason():
    now = dt.datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    assert rate_limit_reason(None, 0, now) is None
    # 20 minutes since the last alert: gap suppression, named.
    r = rate_limit_reason(now - dt.timedelta(minutes=20), 1, now)
    assert r is not None and "gap" in r
    # 40+ minutes: clear again.
    assert rate_limit_reason(now - dt.timedelta(minutes=41), 1, now) is None
    # Day cap, named with the count.
    r = rate_limit_reason(now - dt.timedelta(hours=2), ALERT_DAY_CAP, now)
    assert r is not None and "day_cap" in r


def test_alert_message_carries_slot_values_and_direction():
    snap = {"spot": 717.93, "net_gex": -3.226, "call_wall": 730.0,
            "put_wall": 720.0, "gamma_flip": 722.30, "regime": "slippery"}
    changes = material_changes(MARKS, snap)
    msg = format_alert("QQQ", "11:45", changes, snap)
    assert "Slots: CW 730 / PW 720 / GF 722.30" in msg
    assert "735 → 730" in msg          # direction of the walk, kept
    assert "724.72 → 722.30" in msg
    assert "QQQ" in msg and "11:45" in msg


def test_configured_off_is_a_clean_noop():
    assert not is_configured("gamma") and not is_configured("desk")
    assert post_discord("gamma", "test") is False
    # 'off' short-circuits before any DB work: passing conn=None must
    # not attempt a connection.
    assert claim_and_send("k", "r", "gamma", "test", conn=None) == "off"


def test_desk_event_text():
    m = format_fill("CTNM", "long", "retest_ema_bounce_weekly",
                    14.655, 14.4299, 14.10, "10:00")
    # Reclaim premium stays visible: entry != trigger prints the trigger.
    assert "14.65" in m and "trigger 14.43" in m and "stop 14.10" in m

    m = format_exit("SPY", "long", "stack_fade", 775.1212, 773.21,
                    "stop", -1.03, "15:15")
    assert "-1.03R" in m and "773.21" in m and "entry 775.12" in m
    # R n/a renders as a hole, not a zero.
    m = format_exit("X", "long", "s", 10, 9, "stop", None, "15:00")
    assert "R n/a" in m


def test_429_retry_wait_parsing():
    # 2026-08-19, the morning-burst lesson: the 9:35 open fired 5-7
    # alerts in two seconds, Discord 429'd the overflow, and two real
    # fills went unannounced. Retries honor Discord's own wait.
    from alerts.discord_notify import POST_SPACING_S, retry_after_seconds
    assert retry_after_seconds(429, {"Retry-After": "1.3"}, None) == 1.3
    assert retry_after_seconds(429, {}, {"retry_after": 0.9}) == 0.9
    # A 429 with no wait info still backs off a beat, never zero.
    assert retry_after_seconds(429, {}, None) == 0.5
    # Capped: a scheduler slot is never stalled longer than 10s.
    assert retry_after_seconds(429, {"Retry-After": "120"}, None) == 10.0
    # Non-429s are not retryable rate limits.
    assert retry_after_seconds(404, {"Retry-After": "5"}, None) == 0.0
    # And senders pace bursts under Discord's ~5/2s webhook bucket.
    assert POST_SPACING_S >= 0.4


def test_failed_rows_are_retried_not_tombstoned():
    # A delivered=false row must be re-claimable — by source, the
    # claim path contains the atomic retry UPDATE guarded on
    # delivered=false (two containers race-safe).
    import inspect

    from alerts import discord_notify
    src = inspect.getsource(discord_notify.claim_and_send)
    assert "delivered = false" in src and "RETURNING" in src


def test_megacaps_ride_the_drift_stream():
    # 2026-08-19, Eric: "add the mega caps to the drift alerts." The
    # scanner's seven charts must be in the intraday re-price + drift
    # set — a watch set the alerts don't cover is a _social_block.
    from analysis.gex import DRIFT_TICKERS, INDEXES, MEGACAPS
    assert set(MEGACAPS) == {"META", "MSFT", "AMZN", "TSLA", "GOOGL",
                             "AAPL", "NVDA"}
    assert set(INDEXES) | set(MEGACAPS) == set(DRIFT_TICKERS)
    # And the drift module iterates the combined set, not INDEXES.
    import inspect
    from alerts import gamma_drift
    src = inspect.getsource(gamma_drift)
    assert "DRIFT_TICKERS" in src
    assert "import INDEXES" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
