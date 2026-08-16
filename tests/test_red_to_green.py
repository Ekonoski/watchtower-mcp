"""red_to_green — the flip, pinned.

Born 2026-08-16, Eric's same-evening correction of bull_embed: "No no
no, the red into the green where the RSI's are green and start turning
up." The LAUNCH moment on the BW-3D chart, not the embedded cruise
that follows: money flow in a fresh green run out of REAL red (a
sliver was never red — the COLM/GOOS lesson, mirrored), the green-RSI
pair curling and not spent (the AGO/UNH lesson), RSI rising with room,
waves crossed up, MACD histogram green and expanding, price back above
its 8-bar average. Lifecycle: base_turn → red_to_green → bull_embed.

Standalone per house convention:  python3 tests/test_red_to_green.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.oscillator import (  # noqa: E402
    _confluence, _perf_entry, evaluate_signals)

N = 90


def _frame(red_depth=-9.0, green_run=3, stale=False, sliver=False,
           rsi_fall=False, stoch_spent=False, hist_shrink=False,
           below_avg=False, never_red=False):
    """The flip: flow red for weeks (trough ≤ −4), then a fresh green run;
    RSI rising through the 50s; stoch pair low and curling; waves crossed
    up; MACD hist green and expanding; price reclaiming its average."""
    idx = pd.date_range("2026-01-01", periods=N, freq="D")

    close = np.full(N, 100.0)
    close[:60] = np.linspace(112, 96, 60)            # the decline
    close[60:] = np.linspace(96, 103, N - 60)        # the turn
    if below_avg:
        close[-2:] = [98.0, 97.5]                    # slipped back under

    run = 12 if stale else green_run
    mfv = np.full(N, red_depth if not never_red else 2.0)
    if sliver:
        mfv[:] = -2.5                                # "red" that never was
    if not never_red:
        mfv[-(run):] = np.linspace(1.5, 4.0, run)    # the green run
    else:
        mfv[-N:] = 3.0                               # green forever — no flip

    rsi = np.linspace(38, 57, N)
    if rsi_fall:
        rsi[-3:] = [58, 56, 54]

    sk = np.full(N, 30.0)
    sd = np.full(N, 25.0)
    sk[-5:] = [32, 36, 40, 45, 50]                   # curling up
    if stoch_spent:
        sk[-1], sd[-1] = 84.0, 64.0                  # the AGO pair — ran

    mh = np.linspace(-0.4, 0.35, N)                  # hist green, expanding
    if hist_shrink:
        mh[-4:] = [0.35, 0.25, 0.15, 0.05]
    macd = np.full(N, -0.2)

    wt2 = np.full(N, -15.0)
    wt1 = wt2 + 4.0                                  # crossed up

    return pd.DataFrame({
        "close": close, "high": close + 1, "low": close - 1,
        "volume": np.full(N, 1e6), "wt1": wt1, "wt2": wt2,
        "pctr": np.full(N, -35.0), "rsi": rsi,
        "stoch_k": sk, "stoch_d": sd,
        "macd": macd, "macd_signal": macd - mh, "macd_hist": mh,
        "mf_candle": mfv,
    }, index=idx)


def main():
    # 1) The flip fires with an auditable payload.
    ev = evaluate_signals(_frame())
    rg = ev["signals"].get("red_to_green")
    assert rg, ev["signals"]
    assert rg["green_run"] == 3 and rg["red_depth"] == -9.0, rg
    assert rg["mf"] == 4.0 and rg["stoch_k"] == 50.0, rg

    # 2) Refusals, one leg at a time:
    for kw in (dict(never_red=True),      # green forever — there was no flip
               dict(sliver=True),         # the "red" never reached −4
               dict(stale=True),          # the flip is 12 bars old — ridden
               dict(rsi_fall=True),       # RSIs not turning up
               dict(stoch_spent=True),    # 84/64 — the AGO pair, turn spent
               dict(hist_shrink=True),    # momentum contracting, not expanding
               dict(below_avg=True)):     # price back under its average
        ev = evaluate_signals(_frame(**kw))
        assert "red_to_green" not in ev["signals"], (kw, ev["signals"])

    # 3) Confluence-blind (the family rule).
    df = _frame()
    sig = evaluate_signals(df)["signals"]
    bare = {k: v for k, v in sig.items() if k != "red_to_green"}
    assert _confluence(df, sig, None) == _confluence(df, bare, None)

    # 4) Perf-gate bypass: a flip-only row grades as the bullish claim it
    # is, regardless of the composite's bar.
    ev = evaluate_signals(df)
    pe = _perf_entry("TEST", "3d", df, ev)
    assert pe is not None and pe["direction"] == "bullish", pe
    assert "red_to_green" in pe["signal_type"], pe

    print("ok — the flip fires fresh out of real red with the RSIs "
          "turning, every single-leg flip refuses (no red, sliver red, "
          "stale run, falling RSI, spent stoch, shrinking hist, lost "
          "average), the score stays blind, and flip-only rows grade "
          "as the bullish claim they are")


if __name__ == "__main__":
    main()
