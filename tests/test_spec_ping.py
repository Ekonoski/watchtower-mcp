"""The 🎯 morning tickets ping, pinned (2026-09-01).

  1. Armed gamma specs render as complete bracket tickets.
  2. Zero armed is a stated reading, never silence.
  3. Read-only over the books — by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts.spec_ping import format_tickets  # noqa: E402


def test_armed_specs_render_as_brackets():
    msg = format_tickets(
        [("SPY", "short", "stack_fade_770", 770.08, 771.24, 766.40),
         ("QQQ", "short", "stack_fade_720", 720.0, 721.08, 711.54)], 15)
    assert "SPY SHORT" in msg.replace("*", "")
    assert "entry 770.08" in msg and "stop 771.24" in msg
    assert "target 766.40" in msg
    assert "QQQ SHORT" in msg.replace("*", "")
    assert "Swing book: 15 armed" in msg


def test_zero_armed_is_a_stated_reading():
    msg = format_tickets([], 12)
    assert "0 armed" in msg and "RS leader" in msg
    assert "Swing book: 12 armed" in msg


def test_read_only_by_signature():
    from alerts import spec_ping
    src = inspect.getsource(spec_ping)
    assert "INSERT INTO paper_" not in src
    assert "UPDATE paper_" not in src and "DELETE" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
