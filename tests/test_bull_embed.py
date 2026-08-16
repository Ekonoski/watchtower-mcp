"""bull_embed — the BW-3D full-embed archetype, pinned.

Born 2026-08-16 (Eric: "these create big moves quite often" — the BW 3D
chart). The OPPOSITE end of the lifecycle from the washout family:
money flow green and SUSTAINED, waves riding the upper band, RSI
strong, MACD line and histogram positive, Williams %R embedded near
the ceiling, price stair-stepping above a RISING 8-bar average within
reach of its highs. Bullish only; a named screen, never a gate;
confluence-blind like the rest of the family — and it BYPASSES the
perf-logging confluence gate, because the blended score's bullish
bucket rewards washed-out waves and would systematically refuse the
embed the tracking exists to grade (the sign-flip lesson).

Standalone per house convention:  python3 tests/test_bull_embed.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.oscillator import (  # noqa: E402
    _confluence, _perf_entry, evaluate_signals)

N = 90


def _frame(mf=8.0, mf_blip=False, wt2=60.0, rsi=72.0, hist=0.3,
           pctr=-8.0, fade=False, spike=False):
    """The BW look: staircase to 100 with every panel embedded. Each
    refusal flips exactly one leg."""
    idx = pd.date_range("2026-01-01", periods=N, freq="D")
    close = np.linspace(80.0, 100.0, N)
    if fade:
        close[-3:] = [97.5, 97.0, 96.5]     # still near highs, below the avg
    if spike:
        close[-20] = 120.0                  # old spike: no longer near-high

    mfv = np.full(N, mf)
    if mf_blip:
        mfv[:] = -2.0
        mfv[-3:] = 5.0                      # green NOW but not sustained

    macd = np.full(N, 1.5)
    return pd.DataFrame({
        "close": close, "high": close + 1, "low": close - 1,
        "volume": np.full(N, 1e6),
        "wt1": np.full(N, wt2 + 5.0), "wt2": np.full(N, wt2),
        "pctr": np.full(N, pctr), "rsi": np.full(N, rsi),
        "stoch_k": np.full(N, 80.0), "stoch_d": np.full(N, 70.0),
        "macd": macd, "macd_signal": macd - hist,
        "macd_hist": np.full(N, hist), "mf_candle": mfv,
    }, index=idx)


def main():
    # 1) The archetype fires with an auditable payload.
    ev = evaluate_signals(_frame())
    be = ev["signals"].get("bull_embed")
    assert be, ev["signals"]
    assert be["mf_pos10"] == 10 and be["mf"] == 8.0, be
    assert be["wt2"] == 60.0 and be["rsi"] == 72.0 and be["pctr"] == -8.0, be
    assert be["off_high_pct"] == 0.0, be    # the staircase IS the high

    # 2) One weak leg refuses — each flipped alone:
    for kw in (dict(mf=2.0),          # flow a sliver, not green
               dict(mf_blip=True),    # green NOW but 3 of 10 — not sustained
               dict(wt2=10.0),        # waves mid-band, not riding
               dict(rsi=55.0),        # strength leg
               dict(hist=-0.1),       # MACD not stacked
               dict(pctr=-45.0),      # %R not embedded
               dict(fade=True),       # below the rising 8-bar average
               dict(spike=True)):     # no longer within 5% of its high
        ev = evaluate_signals(_frame(**kw))
        assert "bull_embed" not in ev["signals"], (kw, ev["signals"])

    # 3) Confluence-blind (the family rule: a tiebreaker is a gate in
    # disguise).
    df = _frame()
    sig = evaluate_signals(df)["signals"]
    bare = {k: v for k, v in sig.items() if k != "bull_embed"}
    assert _confluence(df, sig, None) == _confluence(df, bare, None)

    # 4) The perf-gate bypass: an embed chart's composite direction is
    # decoration (its bullish bucket wants washed waves), so an
    # embed-only row logs as the bullish claim it is — regardless of the
    # confluence bar it was never shaped for.
    df = _frame()
    ev = evaluate_signals(df)
    pe = _perf_entry("TEST", "3d", df, ev)
    assert pe is not None, ev
    assert pe["direction"] == "bullish", pe
    assert "bull_embed" in pe["signal_type"] and pe["timeframe"] == "3d", pe

    print("ok — the BW archetype fires with its payload, every single-leg "
          "flip refuses (sliver flow, unsustained blip, mid-band waves, "
          "weak RSI, red MACD, washed %R, faded average, spent high), the "
          "score stays blind, and embed-only rows grade as the bullish "
          "claim they are")


if __name__ == "__main__":
    main()
