"""The 📐 day-bias verdict ping, pinned (2026-08-25).

The contract:
  1. Every message wears the 📐 glyph — the channel's day-bias identity
     (each alert kind wears its own glyph; ⚠ stays a warning marker).
  2. ARMED states the level, the 10:30-only window, the stop, and the
     study prior with n — the message IS the playbook.
  3. STAND-ASIDE renders in full: zero is data, the stand-aside is the
     decision, never a silent skip.
  4. A missing 9:30 open renders as unavailable, never invented.
  5. CANCELLED names the level and the before-10:30 reason (2026-08-25:
     the first cancelled_early happened silently and Eric had to ask).
  6. The module READS the record only — by signature it cannot write
     paper_specs or paper_trades, and delivery is at-most-once via the
     discord_notify_log claim kinds.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts.day_bias_ping import (KIND_CANCEL, KIND_VERDICT,  # noqa: E402
                                  format_cancel, format_hole, format_verdict)

PDH, STOP = 765.22, 759.48


def test_armed_states_the_playbook():
    msg = format_verdict("armed", PDH, STOP, 766.16)
    assert msg.startswith("📐")
    assert "ARMED" in msg
    assert "766.16 > PDH 765.22" in msg
    assert "10:30 ONLY" in msg and "cancels the day" in msg
    assert "Stop 759.48 on 15m closes" in msg
    assert "n=273" in msg  # the prior travels with its n


def test_triggered_reads_as_armed_and_filled():
    msg = format_verdict("triggered", PDH, STOP, 766.16)
    assert "ARMED" in msg and "already filled" in msg


def test_stand_aside_renders_in_full():
    msg = format_verdict("skipped_bias", PDH, STOP, 764.8)
    assert msg.startswith("📐")
    assert "STAND-ASIDE" in msg
    assert "764.80 ≤ PDH 765.22" in msg
    assert "the decision" in msg  # zero is data, stated as such


def test_missing_open_is_unavailable_never_invented():
    msg = format_verdict("armed", PDH, STOP, None)
    assert "unavailable" in msg
    assert "None" not in msg


def test_cancel_names_level_reason_and_bar():
    msg = format_cancel(PDH, "10:00")
    assert msg.startswith("📐")
    assert "CANCELLED" in msg and "765.22" in msg
    assert "before 10:30" in msg and "(bar 10:00)" in msg
    # No recorded touch bar renders without one, not with a fake stamp.
    assert "(bar" not in format_cancel(PDH, None)


def test_cancelled_verdict_is_the_cancel_message():
    assert "CANCELLED" in format_verdict("cancelled", PDH, STOP, 766.16)


def test_hole_is_a_hole_not_a_verdict():
    msg = format_hole()
    assert "unavailable" in msg and "hole" in msg
    assert "ingestion_log" in msg  # points at the diagnosable record


def test_read_only_and_at_most_once_by_signature():
    from alerts import day_bias_ping
    src = inspect.getsource(day_bias_ping)
    assert "UPDATE paper_" not in src
    assert "INSERT INTO paper_" not in src
    assert "DELETE" not in src
    # Delivery rides the claim log with per-day refs.
    assert KIND_VERDICT == "day_bias_verdict"
    assert KIND_CANCEL == "day_bias_cancel"
    assert "claim_and_send" in src
    assert "today.isoformat()" in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
