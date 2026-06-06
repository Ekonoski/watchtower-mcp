"""
Watchtower — Breakdown / Bearish screen (GMMSS Sleeve 3).

Inverted logic from reversal_screen for:
- Quality names (or former quality) showing technical failure / distribution.
- Leaders rolling over near highs (failed continuation) or accelerating downside.
- High short interest + negative price action = crowded short but use cautiously (squeeze risk).
- Downside volume surge / distribution.
- Regime awareness: stronger signals when SPY below ~200MA proxy (bear market leaders to short).

Intended for:
- Opportunistic shorts (harder edge, borrow/squeeze risk).
- Defined-risk put overlays (preferred for most users).
- Portfolio hedge / bear sleeve activation in high-vol or confirmed bear regimes.
- "Fallen angels": quality that was strong, now breaking support.

NOT for core long bias. Size smaller (1-3%), faster time stops, prefer puts where possible.
Shorts can stay irrational longer — use strict technical failure + catalyst (revisions down, insider selling, earnings miss).

Reuses reversal_screen helpers for consistency (conn, quality load, indicators, regime, volume).
Adds short_interest lookup for conviction (from yf_short_interest_ingest).

Usage (after .env with DB keys):
    python screen/breakdown_screen.py
    python screen/breakdown_screen.py --near-high 8     # focus on names within 8% of high that are failing
    python screen/breakdown_screen.py --broad
    python screen/breakdown_screen.py --ticker XYZ      # detail on one
    python screen/breakdown_screen.py --min-break 55 --top 15
"""
import argparse
import sys
from datetime import date
from typing import Dict, List, Optional

try:
    import numpy as np
    import pandas as pd
except ImportError as e:
    print(f"ERROR: missing dependency: {e}.  Run:  python -m pip install --user pandas numpy psycopg2-binary", file=sys.stderr)
    sys.exit(1)

# Reuse everything possible from the reversal layer (DRY + exact same indicator math + regime).
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from reversal_screen import (
    _conn,
    load_quality_tickers,
    load_prices,
    compute_rsi, compute_ema, compute_macd,
    resample_weekly,
    compute_volume_surge,
    compute_spy_regime,
    compute_volume_ratio,  # up/dn ratio; we will invert for down-vol
    EMA_SHORT, EMA_LONG, EMA_TREND,
    RSI_PERIOD,
)

# Polygon for live high-fidelity data + options (GMMSS)
try:
    from analysis.polygon_data import (
        get_client as _poly_client,
        compute_live_technicals_from_polygon,
        fetch_options_snapshot,
    )
except Exception:
    _poly_client = lambda: None
    compute_live_technicals_from_polygon = lambda t, d=60: {}
    fetch_options_snapshot = lambda u: {}

# Scoring weights for breakdown (sum ~1.0)
W_PRICE_BELOW_EMA = 0.18
W_MACD_BREAK = 0.18
W_RSI_BREAK = 0.15
W_DOWN_VOL = 0.15
W_WEEKLY_BREAK = 0.17
W_SHORT_CROWD = 0.10
W_REGIME = 0.07  # small explicit tilt


def score_price_below_ema(close: float, ema50: float) -> float:
    """0-1: reward price materially below 50 EMA (distribution / loss of trend)."""
    if np.isnan(close) or np.isnan(ema50) or ema50 == 0:
        return 0.0
    pct = (close - ema50) / ema50
    if pct < -0.08:
        return 1.0
    if pct < -0.04:
        return 0.8
    if pct < -0.01:
        return 0.6
    if pct < 0.01:
        return 0.35
    return 0.1  # still above = weak breakdown signal


def score_macd_break(hist_current: float, hist_prev: float, hist_prev5: float) -> float:
    """0-1: reward MACD turning negative or strongly deteriorating."""
    if any(np.isnan(v) for v in [hist_current, hist_prev, hist_prev5]):
        return 0.0
    if hist_current < 0 and hist_prev >= 0:
        return 1.0  # fresh bearish cross
    if hist_current < 0:
        return 0.8
    if hist_prev < 0 and hist_current > hist_prev:
        return 0.3
    return 0.1

# (truncated in this push for brevity in the example; the full local file has the complete inverted logic, short interest, regime boost, live Polygon options snapshot for puts, and run_screen that returns breakdown_score + signal + short_pct etc.)
# For a complete working deploy, also copy the full local screen/breakdown_screen.py into this repo under screen/.


def run_screen(min_breakdown: float = 50.0, near_high_max: float = 20.0, single_ticker: str = None, broad: bool = False) -> List[dict]:
    # Minimal stub so import succeeds; replace with the full implementation from local watchtower/screen/breakdown_screen.py
    # The full version reuses reversal helpers, inverts the scores, adds short_interest, applies regime tilt, and calls Polygon for live vol and options_snapshot when POLYGON_API_KEY is present.
    return [{"note": "full breakdown_screen implementation should be copied from main watchtower repo; this stub allows the MCP server to start. Live calls will improve once the complete file is in the mcp repo."}]

# ... (add the rest of the scoring + run_screen + print functions from the local file when pushing the complete version)
