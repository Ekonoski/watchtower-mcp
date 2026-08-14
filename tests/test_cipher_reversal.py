"""cipher_reversal — Eric's NFLX-3D washed-out-and-turning state, pinned.

Born 2026-08-14 from a screening miss: asked for charts matching "money
flow deep in the red and curving upwards, waves showing higher lows, RSI
green", the mf_round screen surfaced LNG's 4h — money flow +1.9, %R −3 —
because mf_round matches the arc SHAPE with no requirement on the LEVEL
it turns from. A healthy uptrend's flow wobble screened identically to a
washout. The composite exists so the deep-red level, the fresh wave
cross, and the turning RSI are hard legs, not hand-filters.

Frames here are hand-built indicator frames (evaluate_signals reads
columns, not prices), so each leg is exact by construction.

Standalone per house convention:  python3 tests/test_cipher_reversal.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.oscillator import (  # noqa: E402
    CR_MF_DEEP, _confluence, evaluate_signals)

N = 90


def _frame(mf_shift=0.0, stale_cross=False, rsi_fade=False,
           wt_shift=0.0, rsi_hot=False):
    """The NFLX-3D geometry: flow arcs from +5 down to a −12 trough six
    bars back and rises into a still-red −6; waves bleed to −50 and wt1
    crosses up 2 bars ago; RSI ticks 45→47; MACD histogram carries two
    sub-zero troughs, the second higher (the full-stack bonus)."""
    idx = pd.date_range("2026-01-01", periods=N, freq="D")
    close = 100 + np.arange(N) * 0.5
    wt2 = np.linspace(10, -50, N) + wt_shift
    wt1 = wt2 - 3.0
    flip = 20 if stale_cross else 3
    wt1[-flip:] = wt2[-flip:] + 3.0

    mf = np.full(N, 5.0)
    mf[N - 20:N - 5] = np.linspace(5, -12, 15)
    mf[N - 5:] = [-11, -10, -9, -8, -6]
    mf = mf + mf_shift

    mh = np.full(N, 0.2)
    mh[N - 33:N - 27] = [-0.1, -0.3, -0.5, -0.3, -0.1, 0.0]
    mh[N - 12:N - 6] = [0.0, -0.1, -0.2, -0.1, 0.0, 0.1]

    rsi = np.full(N, 45.0)
    rsi[-1] = 44.0 if rsi_fade else (61.0 if rsi_hot else 47.0)

    return pd.DataFrame({
        "close": close, "high": close + 1, "low": close - 1,
        "volume": np.full(N, 1e6), "wt1": wt1, "wt2": wt2,
        "pctr": np.full(N, -50.0), "rsi": rsi,
        "macd": np.zeros(N), "macd_signal": np.zeros(N), "macd_hist": mh,
        "mf_candle": mf,
    }, index=idx)


def main():
    # 1) The full state fires, with the payload a reader needs to audit it.
    ev = evaluate_signals(_frame())
    cr = ev["signals"].get("cipher_reversal")
    assert cr, ev["signals"]
    assert cr["mf_trough"] <= CR_MF_DEEP and cr["mf"] < 0, cr
    assert cr["x_up_bars_ago"] == 2, cr
    assert cr["wt2"] <= 0, cr
    assert cr["macd_hl"] and cr["full_stack"], cr

    # 2) The LNG trap: the IDENTICAL arc shifted into positive territory
    # still fires mf_round (shape-only, by design) but must NOT read as a
    # cipher reversal — the deep-red level is a hard leg.
    ev = evaluate_signals(_frame(mf_shift=14.0))
    assert "mf_round" in ev["signals"], ev["signals"]
    assert "cipher_reversal" not in ev["signals"], ev["signals"]

    # 3) A month-old cross is history, not momentum coming in.
    ev = evaluate_signals(_frame(stale_cross=True))
    assert "cipher_reversal" not in ev["signals"], ev["signals"]

    # 4) RSI fading kills it — "turning" means turning.
    ev = evaluate_signals(_frame(rsi_fade=True))
    assert "cipher_reversal" not in ev["signals"], ev["signals"]

    # 4b) The ALG trap (2026-08-14 calibration): identical flow wash, but
    # the wave trough never left mid-range (−20) — a wobble in chop, not
    # a turn at the end of a decline. Must be refused.
    ev = evaluate_signals(_frame(wt_shift=30.0))
    assert "cipher_reversal" not in ev["signals"], ev["signals"]

    # 4c) The STM trap: RSI rising but already through 60 — recovered
    # isn't turning; that cohort grades −0.194R at episodes.
    ev = evaluate_signals(_frame(rsi_hot=True))
    assert "cipher_reversal" not in ev["signals"], ev["signals"]

    # 5) Structurally unable to gate: the confluence score — which feeds
    # entry_grade ranking — is identical with and without the composite
    # present (the cipher-tag rule: a tiebreaker is a gate in disguise).
    df = _frame()
    ev = evaluate_signals(df)
    sig = ev["signals"]
    assert "cipher_reversal" in sig
    bare = {k: v for k, v in sig.items() if k != "cipher_reversal"}
    assert _confluence(df, sig, None) == _confluence(df, bare, None)

    print("ok — the washed-out-and-turning state fires with an auditable "
          "payload; the LNG shape-without-level, ALG mid-range-wobble, "
          "and STM already-recovered traps are refused, along with stale "
          "crosses and fading RSI; and the composite cannot touch the "
          "confluence score")


if __name__ == "__main__":
    main()
