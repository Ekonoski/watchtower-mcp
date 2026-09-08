"""The gamma books' close-through entry gets the swing book's two guards
(2026-09-08, QQQ spec 697): a bar opening beyond the stop is dead on
arrival, and geometry is re-checked at the ACTUAL entry — reward from the
real close must at least equal the real risk (GAMMA_ENTRY_GEOMETRY 1.0;
the 1.5 admission bar at the trigger stays GAMMA_GEOMETRY). A short
'filled' below its own target refuses; every gamma class can still fill
(the assert-admission lesson)."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import paper_trader as pt  # noqa: E402

T = dt.datetime(2026, 9, 8, 9, 30)


def bar(o, c, h, l, minutes=0):
    return (T + dt.timedelta(minutes=minutes), o, c, h, l, 1000.0)


def test_qqq_697_refuses_at_geometry():
    # the recorded 9:30 bar: open 720.909 high 721.886 low 716.56 close 716.72
    verdict, px, kind, why = pt._gamma_fill("short", 720.10, 721.18, 717.56, [bar(720.909, 716.72, 721.886, 716.56)])
    assert verdict == "refuse" and px is None and kind is None
    assert why.startswith("entry_geometry") and "-0.19:1" in why and "1:1" in why


def test_dead_on_arrival_and_a_real_fade():
    # opened above the stop: the level was lost before the setup could act
    verdict, _, _, why = pt._gamma_fill("short", 720.10, 721.18, 717.56, [bar(721.30, 719.50, 721.60, 719.20)])
    assert verdict == "refuse" and why.startswith("dead on arrival")
    # a proper fade: touched the wall, closed back under it, 1.45:1 at the entry — fills
    verdict, px, kind, why = pt._gamma_fill("short", 720.10, 721.18, 717.56, [bar(719.60, 719.70, 720.30, 719.40)])
    assert (verdict, px, kind, why) == ("fill", 719.70, "close_through", None)
    # touched but did not close back through: no fill yet, no refusal
    assert pt._gamma_fill("short", 720.10, 721.18, 717.56, [bar(719.60, 720.40, 720.60, 719.40)]) == (None, None, None, None)
    # the 2026-08-21 shape: a close-through 3.40 under the stack, 0.34:1 — refused
    verdict, _, _, why = pt._gamma_fill("short", 715.00, 716.07, 710.06, [bar(714.80, 711.60, 715.20, 711.40)])
    assert verdict == "refuse" and "0.34:1" in why


def test_every_gamma_class_can_fill_and_the_near_target_long_cannot():
    # flip-hold long with a 50-cent confirmation premium: 1.43:1 at the entry — fills
    verdict, px, kind, _ = pt._gamma_fill("long", 768.06, 766.91, 771.00, [bar(767.80, 768.56, 768.70, 767.50)])
    assert (verdict, px, kind) == ("fill", 768.56, "close_through")
    # the 2026-08-27 SPY flip-hold shape: 769.19 against a 770 target, 0.36:1 — refused
    verdict, _, _, why = pt._gamma_fill("long", 768.06, 766.91, 770.00, [bar(767.80, 769.19, 769.30, 767.50)])
    assert verdict == "refuse" and "0.36:1" in why
    # wall fade with room: fills
    assert pt._gamma_fill("short", 775.00, 776.16, 772.50, [bar(774.70, 774.36, 775.20, 774.10)])[0] == "fill"
    # a long that opened under the stop is dead on arrival
    assert pt._gamma_fill("long", 768.06, 766.91, 771.00, [bar(766.50, 768.40, 768.60, 766.30)])[0] == "refuse"


def test_one_geometry_source_and_the_loop_uses_it():
    assert pt.GAMMA_GEOMETRY == 1.5 and pt.GAMMA_ENTRY_GEOMETRY == 1.0
    src = inspect.getsource(pt.build_gamma_specs)
    assert src.count("GAMMA_GEOMETRY *") == 3 and "1.5 *" not in src
    whole = inspect.getsource(pt)
    assert "_gamma_fill(direction, trig, stop, tgt, live_bars)" in whole
    assert "GAMMA_ENTRY_GEOMETRY)" in inspect.getsource(pt._gamma_fill)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
