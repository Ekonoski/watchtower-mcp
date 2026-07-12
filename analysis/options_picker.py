"""
Watchtower options contract picker — turns a pattern setup into a ticket.

The system already produces the hard parts of an options decision: entry,
stop, target, measured resolution time (Est DTE, from the pattern timing
backtest), and higher-timeframe context. This module adds the last mile
via Polygon's options snapshot (Options Starter: chains with greeks, IV,
open interest; 15-minute delayed — decision data, not execution data):

  build_ticket(ticker) ->
    - expiry: the nearest expiration at/after the Est-DTE floor (2x the
      p75 resolution time — the "buy twice the time you think you need"
      rule), from the pattern's own measured history when available
    - directional leg: the ~0.65-delta call (bullish) / put (bearish) at
      that expiry, liquidity-gated on open interest
    - vertical: long strike nearest the ENTRY, short strike nearest the
      TARGET — the pattern's own geometry as a defined-risk spread
    - IV context: ATM IV vs the underlying's 21-day realized vol (crude
      rich/cheap gauge until our own IV-rank history accumulates)
    - earnings flag: report date inside the option's life (calendar tab
      data) — decide in advance whether the position rides through it

Prices come from last trade / day close (Starter has no quotes feed) —
fine on liquid strikes, which is what the OI gate enforces; the broker's
screen is the final price check at execution.
"""
import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)

MIN_OI = 100          # liquidity gate; relaxed to 25 with a warning
TARGET_DELTA = 0.65   # directional leg


def _fetch_chain(ticker: str, contract_type: str, exp_gte: date, exp_lte: date,
                 strike_lo: float, strike_hi: float, limit: int = 250) -> list:
    """Option contract snapshots for one underlying, filtered server-side
    where Polygon allows and client-side for the rest. Uses the same
    get_snapshot_all idiom as fetch_options_snapshot (version-safe)."""
    from analysis.polygon_data import get_client
    client = get_client()
    if not client:
        return []
    out = []
    try:
        snaps = client.get_snapshot_all(
            "options",
            params={
                "underlying_ticker": ticker,
                "contract_type": contract_type,
                "expiration_date.gte": exp_gte.isoformat(),
                "expiration_date.lte": exp_lte.isoformat(),
                "strike_price.gte": strike_lo,
                "strike_price.lte": strike_hi,
            },
            limit=limit,
        )
        for s in snaps:
            det = getattr(s, "details", None)
            if det is None:
                continue
            greeks = getattr(s, "greeks", None)
            day = getattr(s, "day", None)
            lt = getattr(s, "last_trade", None)
            price = getattr(day, "close", None) or getattr(lt, "price", None)
            out.append({
                "occ": getattr(det, "ticker", None),
                "strike": getattr(det, "strike_price", None),
                "exp": str(getattr(det, "expiration_date", "")),
                "delta": getattr(greeks, "delta", None) if greeks else None,
                "iv": getattr(s, "implied_volatility", None),
                "oi": getattr(s, "open_interest", None),
                "vol": getattr(day, "volume", None) if day else None,
                "last": round(float(price), 2) if price else None,
            })
    except Exception as e:
        log.warning(f"[options] chain fetch {ticker} failed: {e}")
        return []
    return [c for c in out if c["strike"] and c["exp"]]


def _realized_vol_21d(conn, ticker: str):
    """Annualized 21-day close-to-close volatility from daily_prices."""
    import math
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT close FROM daily_prices WHERE ticker=%s
                ORDER BY trade_date DESC LIMIT 22
            """, (ticker,))
            closes = [float(r[0]) for r in cur.fetchall()][::-1]
        if len(closes) < 15:
            return None
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252)
    except Exception:
        return None


def _earnings_inside(conn, ticker: str, until: date):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT report_date, time_of_day FROM earnings_calendar
                WHERE ticker=%s AND report_date >= CURRENT_DATE
                  AND report_date <= %s
                ORDER BY report_date LIMIT 1
            """, (ticker, until))
            r = cur.fetchone()
        return {"date": str(r[0]), "when": r[1] or ""} if r else None
    except Exception:
        return None


def build_ticket(ticker: str) -> dict:
    """Full ticket for the ticker's best live pattern. Returns {} when
    there's no live pattern; {'error': ...} on data problems."""
    from screen.reversal_screen import _conn
    from analysis.pattern_backtest import estimate_resolution, timing_stats

    ticker = ticker.upper().strip()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pattern, direction, status, trigger_price, invalid_level,
                       target, last_close, timeframe, anchor_date, score
                FROM pattern_scan
                WHERE ticker = %s AND timeframe IN ('daily','weekly')
                ORDER BY score DESC NULLS LAST LIMIT 1
            """, (ticker,))
            row = cur.fetchone()
        if not row:
            return {}
        (pattern, direction, status, trigger, invalid, target,
         last_close, tf, anchor_date, score) = row
        trigger, invalid, target, last_close = (
            float(trigger), float(invalid) if invalid is not None else None,
            float(target), float(last_close))

        est = estimate_resolution(pattern, tf, anchor_date, timing_stats(conn)) or {}
        dte = int(est.get("dte") or 60)
        today = date.today()
        exp_gte = today + timedelta(days=max(dte - 7, 14))
        exp_lte = today + timedelta(days=int(dte * 2.2))

        bull = direction == "bullish"
        ctype = "call" if bull else "put"
        lo = min(trigger, target) * (0.75 if bull else 0.85)
        hi = max(trigger, target) * (1.10 if bull else 1.25)
        chain = _fetch_chain(ticker, ctype, exp_gte, exp_lte, lo, hi)
        if not chain:
            return {"error": "no_chain", "ticker": ticker,
                    "note": "Chain empty — entitlement, symbol, or window issue."}

        liquid = [c for c in chain if (c["oi"] or 0) >= MIN_OI]
        oi_note = None
        if not liquid:
            liquid = [c for c in chain if (c["oi"] or 0) >= 25]
            oi_note = f"thin chain — OI gate relaxed to 25 (nothing had {MIN_OI}+)"
        if not liquid:
            return {"error": "illiquid", "ticker": ticker,
                    "note": "No strikes with meaningful open interest."}

        exps = sorted({c["exp"] for c in liquid})
        expiry = exps[0]
        at_exp = [c for c in liquid if c["exp"] == expiry]

        # Directional leg: delta nearest ±TARGET_DELTA (fall back to the
        # strike nearest the trigger when greeks are missing).
        want = TARGET_DELTA if bull else -TARGET_DELTA
        with_delta = [c for c in at_exp if c["delta"] is not None]
        if with_delta:
            leg = min(with_delta, key=lambda c: abs(c["delta"] - want))
        else:
            leg = min(at_exp, key=lambda c: abs(c["strike"] - trigger))

        # Vertical: long nearest the entry, short nearest the target.
        long_leg = min(at_exp, key=lambda c: abs(c["strike"] - trigger))
        short_leg = min(at_exp, key=lambda c: abs(c["strike"] - target))
        vertical = None
        if long_leg["strike"] != short_leg["strike"]:
            width = abs(short_leg["strike"] - long_leg["strike"])
            debit = None
            if long_leg["last"] and short_leg["last"]:
                debit = round(long_leg["last"] - short_leg["last"], 2)
            vertical = {"long": long_leg, "short": short_leg,
                        "width": round(width, 2), "est_debit": debit,
                        "max_value": round(width, 2)}

        atm = min(at_exp, key=lambda c: abs(c["strike"] - last_close))
        rv = _realized_vol_21d(conn, ticker)
        iv_ctx = None
        if atm["iv"] and rv:
            ratio = atm["iv"] / rv
            iv_ctx = {"atm_iv": round(atm["iv"], 3), "realized_21d": round(rv, 3),
                      "ratio": round(ratio, 2),
                      "read": ("rich — favor spreads" if ratio >= 1.3 else
                               "cheap — straight options fine" if ratio <= 0.9 else
                               "fair")}

        exp_date = date.fromisoformat(expiry)
        earnings = _earnings_inside(conn, ticker, exp_date)

        return {
            "ticker": ticker, "pattern": pattern, "timeframe": tf,
            "direction": direction, "status": status, "score": float(score or 0),
            "entry": trigger, "stop": invalid, "target": target,
            "last_close": last_close,
            "est_weeks": [est.get("weeks_lo"), est.get("weeks_hi")],
            "dte_floor": dte, "expiry": expiry,
            "expiries_available": exps[:4],
            "directional": leg, "vertical": vertical,
            "iv": iv_ctx, "earnings": earnings, "oi_note": oi_note,
        }
    finally:
        conn.close()


def entitlement_probe() -> str:
    """One-line boot check: can we see a liquid chain with greeks/IV?
    Logged at deploy so entitlement problems surface in Railway logs."""
    try:
        today = date.today()
        chain = _fetch_chain("SPY", "call", today + timedelta(days=20),
                             today + timedelta(days=60), 500, 900, limit=50)
        n = len(chain)
        greeks = sum(1 for c in chain if c["delta"] is not None)
        iv = sum(1 for c in chain if c["iv"] is not None)
        msg = (f"[options] entitlement probe: SPY chain {n} contracts, "
               f"{greeks} with greeks, {iv} with IV")
        log.info(msg)
        return msg
    except Exception as e:
        msg = f"[options] entitlement probe FAILED: {e}"
        log.warning(msg)
        return msg
