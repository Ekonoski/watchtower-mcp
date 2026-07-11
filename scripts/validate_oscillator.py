"""
Watchtower Oscillator validation — plots + last-10-bar values for manual
comparison against TradingView panels (Cipher defaults; %R 28 + EMA 21;
MACD 12/26/9).

Covers: SOFI daily + 3-day resample, ELF daily + 2-day resample,
ZS weekly + 4h. Daily/weekly bars come from the DB; 4h needs
POLYGON_API_KEY and is skipped (with a note) when it's absent.

Run from the repo root:  python scripts/validate_oscillator.py [outdir]

For each (ticker, timeframe) it writes <outdir>/osc_<ticker>_<tf>.png with
four panels — price, WaveTrend (wt1/wt2/wt_diff + both money-flow variants),
%R + EMA guide, MACD — and prints the last 10 confirmed-bar values per
series. Compare wave shape, cross locations, and %R values; they should
match TradingView within rounding. NOTE: 2-day/3-day bars here are anchored
at the END of the series (newest bar always complete); TradingView anchors
at the start of its history, so multi-day panels can be phase-shifted by
one session — judge those by shape, not exact bar alignment.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from analysis.oscillator import (compute_oscillator, resample_days,
                                 resample_weekly, fetch_4h_confirmed,
                                 WT_INNER, WT_OUTER)

CASES = [("SOFI", "daily"), ("SOFI", "3d"), ("ELF", "daily"), ("ELF", "2d"),
         ("ZS", "weekly"), ("ZS", "4h")]

SERIES = ["wt1", "wt2", "wt_diff", "mf_candle", "mf_volume",
          "rsi", "stoch_k", "stoch_d", "pctr", "pctr_ema",
          "macd", "macd_signal", "macd_hist"]


def _daily_from_db(ticker: str) -> pd.DataFrame:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT trade_date, COALESCE(open, close), COALESCE(high, close),
                       COALESCE(low, close), close, COALESCE(volume, 0)
                FROM daily_prices
                WHERE ticker = %s AND trade_date >= CURRENT_DATE - 900
                ORDER BY trade_date
            """, (ticker,))
            rows = cur.fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = \
        df[["open", "high", "low", "close", "volume"]].astype(float)
    return df.set_index("ts")


def bars_for(ticker: str, tf: str) -> pd.DataFrame:
    if tf == "4h":
        return fetch_4h_confirmed(ticker, days=200)
    daily = _daily_from_db(ticker)
    if tf == "daily":
        return daily
    if tf == "weekly":
        return resample_weekly(daily)
    if tf in ("2d", "3d"):
        return resample_days(daily, int(tf[0]))
    raise ValueError(tf)


def plot_case(ticker: str, tf: str, df: pd.DataFrame, outdir: str) -> str:
    o = compute_oscillator(df).iloc[-160:]
    x = range(len(o))
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True,
                             gridspec_kw={"height_ratios": [2, 2, 1, 1]})
    fig.suptitle(f"{ticker} — {tf} (confirmed bars, last {len(o)})", fontsize=13)

    axes[0].plot(x, o["close"], lw=1.2, color="black")
    axes[0].set_ylabel("close")
    axes[0].grid(alpha=0.25)

    ax = axes[1]
    ax.fill_between(x, 0, o["wt_diff"], color="gold", alpha=0.6, label="wt_diff")
    ax.plot(x, o["wt1"], color="#19c8ff", lw=1.4, label="wt1")
    ax.plot(x, o["wt2"], color="#1969ff", lw=1.2, label="wt2")
    ax.plot(x, o["mf_candle"], color="green", lw=1.0, alpha=0.8, label="mf_candle")
    ax.plot(x, o["mf_volume"] * 50, color="purple", lw=1.0, alpha=0.7,
            label="mf_volume ×50")
    for lvl, ls in ((WT_INNER, "--"), (WT_OUTER, ":")):
        ax.axhline(lvl, color="red", ls=ls, lw=0.7)
        ax.axhline(-lvl, color="green", ls=ls, lw=0.7)
    ax.axhline(0, color="gray", lw=0.6)
    ax.set_ylabel("WaveTrend / MF")
    ax.legend(loc="upper left", fontsize=7, ncol=5)
    ax.grid(alpha=0.25)

    ax = axes[2]
    ax.plot(x, o["pctr"], color="#19c8ff", lw=1.2, label="%R(28)")
    ax.plot(x, o["pctr_ema"], color="orange", lw=1.1, label="EMA21(%R)")
    ax.axhline(-20, color="red", ls="--", lw=0.7)
    ax.axhline(-80, color="green", ls="--", lw=0.7)
    ax.set_ylabel("%R")
    ax.legend(loc="upper left", fontsize=7, ncol=2)
    ax.grid(alpha=0.25)

    ax = axes[3]
    colors = ["#2bd576" if v >= 0 else "#ff5c5c" for v in o["macd_hist"]]
    ax.bar(x, o["macd_hist"], color=colors, alpha=0.7, label="hist")
    ax.plot(x, o["macd"], color="#19c8ff", lw=1.1, label="macd")
    ax.plot(x, o["macd_signal"], color="orange", lw=1.0, label="signal")
    ax.set_ylabel("MACD")
    ax.legend(loc="upper left", fontsize=7, ncol=3)
    ax.grid(alpha=0.25)

    path = os.path.join(outdir, f"osc_{ticker}_{tf}.png")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)

    print(f"\n=== {ticker} {tf} — last 10 confirmed bars ===")
    tail = compute_oscillator(df).iloc[-10:]
    with pd.option_context("display.width", 200, "display.max_columns", 30,
                           "display.float_format", lambda v: f"{v:8.2f}"):
        print(tail[["close"] + SERIES].round(2).to_string())
    return path


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)
    for ticker, tf in CASES:
        try:
            df = bars_for(ticker, tf)
            if len(df) < 70:
                print(f"!! {ticker} {tf}: only {len(df)} bars — skipped "
                      f"(4h needs POLYGON_API_KEY)" if tf == "4h"
                      else f"!! {ticker} {tf}: only {len(df)} bars — skipped")
                continue
            print("wrote", plot_case(ticker, tf, df, outdir))
        except Exception as e:
            print(f"!! {ticker} {tf} failed: {e}")


if __name__ == "__main__":
    main()
