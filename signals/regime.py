"""
GMMSS — Regime Allocator & Dynamic Sleeve Weights

This is the "multi-regime" decision layer for the full system.

Core logic:
- Bull regime (SPY above ~200MA proxy): Favor Sleeve 1 (Reversal-Quality) + Sleeve 2 (Momentum / Up-and-Comers).
- Bear regime (SPY below): Reduce gross long exposure, activate Sleeve 3 (Bearish/Breakdown/Puts) for protection + opportunistic profits.
- Neutral / chop: Smaller overall size, higher bar for entries, balanced sleeves.

Uses live Polygon data (when key present) for the most current regime read.
Falls back to DB SPY history.

Outputs feed:
- Daily brief (top section + notes)
- Position sizing (scale sizes by regime)
- Grok theses (regime fit + allocation context)
- Research / MCP

User can manually use breakdown outputs + stock signals to buy puts/calls while options tier is off.
"""

from datetime import date
from typing import Dict, Any, Optional

# Reuse existing regime primitive (SPY > ~200MA)
from screen.reversal_screen import compute_spy_regime

# Live data preference (Polygon after key added)
try:
    from analysis.polygon_data import fetch_recent_bars
except Exception:
    fetch_recent_bars = None


def compute_live_spy_regime(days: int = 300) -> Optional[bool]:
    """Return True (bull) if SPY > ~200MA proxy using freshest data.
    Prefers Polygon bars (clean, live) when available. Robust to shorter histories.
    """
    # Try Polygon first for live accuracy (GMMSS + Polygon key)
    if fetch_recent_bars:
        try:
            bars = fetch_recent_bars("SPY", days=days)
            closes = [float(b["close"]) for b in bars if b.get("close") is not None]
            if len(closes) >= 200:
                ma200 = sum(closes[-200:]) / 200
                latest = closes[-1]
                return latest > ma200
            elif len(closes) >= 50:
                # graceful shorter MA for very new data
                ma = sum(closes[-min(50, len(closes)):]) / min(50, len(closes))
                return closes[-1] > ma
        except Exception:
            pass

    # Fallback: let the caller supply a df or use DB path via reversal_screen pattern
    return None


def get_regime_allocation(spy_df: Any = None) -> Dict[str, Any]:
    """Main entry point. Returns regime classification + recommended sleeve weights + exposure guidance.

    Call this early in daily pipeline / research.

    Returns dict with:
      - regime: "bull" | "bear" | "neutral"
      - spy_regime: bool or None
      - weights: { "reversal": 0.xx, "momentum": 0.xx, "bearish": 0.xx, "event": 0.xx }
      - gross_long_target: 0.0-1.0 suggested overall long exposure
      - notes: human readable guidance (mentions manual puts/calls option)
      - vol_context: placeholder for future volatility tilt
    """
    # Determine regime
    spy_regime = None
    if spy_df is not None:
        try:
            spy_regime = compute_spy_regime(spy_df)
        except Exception:
            pass

    if spy_regime is None:
        spy_regime = compute_live_spy_regime()

    if spy_regime is True:
        regime = "bull"
        weights = {
            "reversal": 0.50,      # Core quality turning — still the highest edge sleeve
            "momentum": 0.35,      # Up-and-comers in heating sectors (your 10x hunters)
            "bearish": 0.05,       # Light hedge / opportunistic
            "event": 0.10,
        }
        gross_long_target = 0.85
        notes = ("Bull regime (SPY above ~200MA proxy). "
                 "Heavy reversal + momentum/up-and-comer bias. "
                 "Use momentum outputs for calls where conviction is high. "
                 "Keep small bearish sleeve for tail protection.")
    elif spy_regime is False:
        regime = "bear"
        weights = {
            "reversal": 0.25,      # Still take high-quality reversals but smaller
            "momentum": 0.10,      # Very selective — only the strongest up-and-comers
            "bearish": 0.45,       # Primary sleeve: breakdowns + puts
            "event": 0.20,
        }
        gross_long_target = 0.40
        notes = ("Bear regime (SPY below ~200MA proxy). "
                 "Reduce gross long exposure. Activate breakdown/put sleeve aggressively. "
                 "Use breakdown stock signals + volume surge / short interest to buy puts manually "
                 "(or calls on strong relative strength bounces). "
                 "Patience on longs — wait for clearer inflection + regime improvement.")
    else:
        regime = "neutral"
        weights = {
            "reversal": 0.40,
            "momentum": 0.25,
            "bearish": 0.15,
            "event": 0.20,
        }
        gross_long_target = 0.60
        notes = ("Neutral / choppy regime. Smaller overall size, higher bar for entries. "
                 "Balanced sleeves. Use breakdown names opportunistically for puts/calls on your stock signals. "
                 "Emphasize multi-signal convergence and book edge before sizing.")

    return {
        "regime": regime,
        "spy_regime": spy_regime,
        "weights": weights,
        "gross_long_target": gross_long_target,
        "notes": notes,
        "vol_context": "volatility tilt not yet implemented (future: expand on volatility_metrics table)",
        "as_of": date.today().isoformat(),
    }


def format_regime_for_report(alloc: Dict[str, Any]) -> str:
    """Pretty one-paragraph summary for daily email / brief."""
    w = alloc["weights"]
    return (
        f"REGIME: {alloc['regime'].upper()} (SPY regime={alloc['spy_regime']})  |  "
        f"Gross long target ~{alloc['gross_long_target']*100:.0f}%  |  "
        f"Reversal {w['reversal']*100:.0f}% / Momentum {w['momentum']*100:.0f}% / "
        f"Bearish {w['bearish']*100:.0f}% / Event {w['event']*100:.0f}%\n"
        f"  {alloc['notes']}"
    )
