"""The Williams-%R higher-low family — pctr_hl and base_turn, pinned.

Calibrated 2026-08-15 against Eric's charts. CHWY is the archetype: two
%R(28) floor troughs rising while price bases. NI defined the
saturation TAG — a 0.5-point "higher low" between two bars pinned at
−99 is the indicator clamped at its bound, so the pair fires marked
`shallow` and ranks last rather than being skipped (Eric: "sometimes
those run like they did with CHWY"; flavors grade separately live).
MARA is the refusal that defined stabilization — its tape was still
printing new 30-bar lows, so every wash metric was maxed by an ONGOING
collapse, and that guard stays hard. base_turn (the SNAP look) is the same structure
with everything confirming: MACD histogram green under a still-negative
line, waves crossed up and lifting, RSI mid-band, flow out of deep red,
price above its 8-bar average.

Standalone per house convention:  python3 tests/test_pctr_hl_family.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.oscillator import (  # noqa: E402
    _confluence, evaluate_signals)

N = 90


def _frame(saturated=False, still_falling=False, spent=False, turned=False):
    """Base: price fell, based, and stopped making lows; %R troughs
    −92 → −78 with the line now at −55. Variants flip one leg each."""
    idx = pd.date_range("2026-01-01", periods=N, freq="D")

    close = np.full(N, 100.0)
    close[:40] = np.linspace(120, 100, 40)          # the decline
    close[40:] = 100 + 2 * np.sin(np.arange(N - 40) / 4.0)   # the base, peak ~102
    if still_falling:
        close[-3:] = [96.5, 96.0, 95.5]             # new lows into today
    if turned:
        # Above the 8-bar average, still under the base high (pre-breakout).
        close[-8:] = [99.6, 99.8, 100.0, 100.1, 100.2, 100.3, 100.45, 100.55]

    pctr = np.full(N, -50.0)
    lows = (-99.2, -98.7) if saturated else (-92.0, -78.0)
    # trough 1 at N-22, trough 2 at N-8 — both confirmed (2 bars each side)
    pctr[N-24:N-19] = [-80, -88, lows[0], -85, -75]
    pctr[N-10:N-5] = [-70, -74, lows[1], -72, -68]
    pctr[N-5:] = [-68, -64, -60, -58, -55]
    if spent:
        pctr[-1] = -30.0                             # lifted too far for pctr_hl

    wt2 = np.full(N, -30.0)
    wt1 = wt2 - 3.0
    if turned:
        wt1[-6:] = wt2[-6:] + 5.0                    # crossed up, wt2 mid-zone

    mh = np.full(N, -0.5)
    macd = np.full(N, -2.0)
    if turned:
        mh[-4:] = [0.05, 0.1, 0.15, 0.2]             # hist green, line under water

    rsi = np.full(N, 48.0)
    mf = np.full(N, -6.0)

    return pd.DataFrame({
        "close": close, "high": close + 1, "low": close - 1,
        "volume": np.full(N, 1e6), "wt1": wt1, "wt2": wt2,
        "pctr": pctr, "rsi": rsi,
        "stoch_k": np.full(N, 30.0), "stoch_d": np.full(N, 20.0),
        "macd": macd, "macd_signal": macd - mh, "macd_hist": mh,
        "mf_candle": mf,
    }, index=idx)


def main():
    # 1) The CHWY shape fires pctr_hl with an auditable payload, and a
    # real-lift pair is NOT shallow.
    ev = evaluate_signals(_frame())
    ph = ev["signals"].get("pctr_hl")
    assert ph, ev["signals"]
    assert ph["low1"] == -92.0 and ph["low2"] == -78.0 and ph["lift"] == 14.0, ph
    assert ph["pctr"] <= -45 and ph["stable_bars"] >= 3, ph
    assert ph["shallow"] is False, ph

    # 2) The NI look — a 0.5-point pair pinned at −99 — fires TAGGED
    # shallow (Eric: small higher lows sometimes run; grade, don't skip).
    # What actually separated NI from CHWY was its still-falling tape,
    # and that guard is case 3.
    ev = evaluate_signals(_frame(saturated=True))
    ph = ev["signals"].get("pctr_hl")
    assert ph and ph["shallow"] is True, ev["signals"]

    # 3) The MARA trap: tape still printing new lows — refused.
    ev = evaluate_signals(_frame(still_falling=True))
    assert "pctr_hl" not in ev["signals"], ev["signals"]

    # 4) Spent %R (−30) drops pctr_hl (the early flavor) — but the pair
    # structure itself remains valid for base_turn once the turn confirms.
    ev = evaluate_signals(_frame(spent=True))
    assert "pctr_hl" not in ev["signals"], ev["signals"]

    # 5) The SNAP look: same structure + everything turning → base_turn,
    # while pctr_hl still fires when %R remains unspent.
    ev = evaluate_signals(_frame(turned=True))
    bt = ev["signals"].get("base_turn")
    assert bt, ev["signals"]
    assert bt["macd_hist"] > 0 and bt["macd"] <= 0, bt
    assert bt["low1"] == -92.0 and bt["low2"] == -78.0, bt

    # 6) Neither signal can touch the confluence score (a tiebreaker is a
    # gate in disguise — the family rule).
    df = _frame(turned=True)
    sig = evaluate_signals(df)["signals"]
    bare = {k: v for k, v in sig.items() if k not in ("pctr_hl", "base_turn")}
    assert _confluence(df, sig, None) == _confluence(df, bare, None)

    print("ok — the CHWY shape fires unshallow, the NI saturated pair "
          "fires tagged shallow (graded, never skipped), the MARA "
          "still-falling trap stays refused, spent %R drops the early "
          "flavor, the SNAP look fires base_turn, and neither signal "
          "can touch the confluence score")


if __name__ == "__main__":
    main()
