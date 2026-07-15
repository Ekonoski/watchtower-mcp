"""
Watchtower Gamma — dealer-positioning (GEX) levels, computed in-house.

Everything the paid gamma-map products sell reduces to published math on
data we already pull nightly: per-contract GEX = gamma x OI x 100 x
spot^2 x 0.01 (dollar gamma per 1% move), calls counted positive and
puts negative under the category-standard dealers-long-calls/short-puts
convention. From the per-strike profile fall the three levels:

  call wall   strike with the largest call-side GEX — where dealer
              hedging leans AGAINST rallies (sell-into-strength supply)
  put wall    strike with the largest put-side |GEX| — hedging leans
              against declines (buy-the-dip support)
  gamma flip  spot level where NET dealer gamma crosses zero. Above it,
              hedging dampens moves (pinning tape); below it, hedging
              amplifies them (slippery tape). Found by re-pricing every
              contract's Black-Scholes gamma across a spot grid — the
              flip is a counterfactual, not a chain readout.

Honesty notes, straight from the research that motivated this module:
  - OI updates ONCE per day (overnight) for everyone, vendors included.
    These are session levels, recomputed nightly; "intraday updates"
    elsewhere are re-pricings of the same stale OI.
  - The evidence says NET-GEX SIGN is a regime label (calm vs slippery),
    not a return forecast; the sign convention is least reliable on
    speculative single names. We compute indexes + watchlist only.
  - The pinning effect is real but bps-scale (JFE 2005); modern 0DTE
    flow may flip it toward amplification near big strikes. Treat walls
    as S/R candidates to confirm on the tape, not commandments.

Results land in gex_levels (ticker, as_of) — one row per session,
stamped with iv_session_date() so late-evening runs label the right day.
"""
import logging
import math
import time
from datetime import date, timedelta

log = logging.getLogger(__name__)

INDEXES = ("SPY", "QQQ", "IWM", "DIA")
EXP_WINDOW_DAYS = 120     # gamma beyond ~4 months is noise at this grain
MAX_CONTRACTS = 6000      # safety cap per underlying
MIN_CONTRACTS = 50        # below this the chain is too thin to map
RISK_FREE = 0.04


def _fetch_gex_chain(ticker: str) -> tuple:
    """(spot, [{strike, exp_days, iv, oi, gamma, is_call}]) for every
    contract with OI inside the expiry window."""
    from itertools import islice
    from analysis.polygon_data import get_client
    client = get_client()
    if not client:
        return None, []
    today = date.today()
    spot = None
    out = []
    try:
        snaps = islice(client.list_snapshot_options_chain(
            ticker,
            params={
                "expiration_date.gte": today.isoformat(),
                "expiration_date.lte": (today + timedelta(
                    days=EXP_WINDOW_DAYS)).isoformat(),
                "limit": 250,
            },
        ), MAX_CONTRACTS)
        for s in snaps:
            det = getattr(s, "details", None)
            if det is None:
                continue
            if spot is None:
                ua = getattr(s, "underlying_asset", None)
                spot = getattr(ua, "price", None) if ua else None
            oi = getattr(s, "open_interest", None)
            greeks = getattr(s, "greeks", None)
            gamma = getattr(greeks, "gamma", None) if greeks else None
            iv = getattr(s, "implied_volatility", None)
            strike = getattr(det, "strike_price", None)
            ctype = str(getattr(det, "contract_type", "")).lower()
            exp = str(getattr(det, "expiration_date", ""))
            if not oi or not strike or ctype not in ("call", "put"):
                continue
            try:
                exp_days = (date.fromisoformat(exp) - today).days
            except ValueError:
                continue
            out.append({"strike": float(strike), "exp_days": max(exp_days, 0),
                        "iv": float(iv) if iv else None,
                        "oi": int(oi),
                        "gamma": float(gamma) if gamma is not None else None,
                        "is_call": ctype == "call"})
    except Exception as e:
        log.warning(f"[gex] chain fetch {ticker} failed: {e}")
    return spot, out


def _bs_gamma(spot: float, strike: float, iv: float, t_years: float) -> float:
    """Black-Scholes gamma — same for calls and puts."""
    if iv <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    st = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (RISK_FREE + iv * iv / 2) * t_years) / st
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    return pdf / (spot * st)


def _net_gex_at(contracts: list, spot: float) -> float:
    """Net dealer GEX ($ per 1% move) with every contract's gamma
    re-priced at a hypothetical spot — the input to the flip search."""
    total = 0.0
    for c in contracts:
        if c["iv"] is None:
            continue
        g = _bs_gamma(spot, c["strike"], c["iv"],
                      max(c["exp_days"], 0.5) / 365.0)
        sign = 1.0 if c["is_call"] else -1.0
        total += sign * g * c["oi"] * 100.0 * spot * spot * 0.01
    return total


def compute_gex(ticker: str, fallback_spot: float = None) -> dict:
    """One underlying: walls, flip, net GEX, top strikes. {} if the chain
    is too thin or data is missing. fallback_spot covers plans where the
    chain snapshot's nested underlying quote is not populated (ours) —
    same reason options_picker always passes the close in from
    daily_prices instead of trusting underlying_asset.price."""
    spot, contracts = _fetch_gex_chain(ticker)
    if spot is None:
        spot = fallback_spot
    if spot is None or len(contracts) < MIN_CONTRACTS:
        return {}
    per_strike: dict = {}
    call_side: dict = {}
    put_side: dict = {}
    net = 0.0
    for c in contracts:
        if c["gamma"] is None:
            continue
        g = c["gamma"] * c["oi"] * 100.0 * spot * spot * 0.01
        k = c["strike"]
        if c["is_call"]:
            call_side[k] = call_side.get(k, 0.0) + g
            per_strike[k] = per_strike.get(k, 0.0) + g
            net += g
        else:
            put_side[k] = put_side.get(k, 0.0) + g
            per_strike[k] = per_strike.get(k, 0.0) - g
            net -= g
    if not per_strike:
        return {}
    call_wall = max(call_side, key=call_side.get) if call_side else None
    put_wall = max(put_side, key=put_side.get) if put_side else None

    # Flip: sweep +/-15% around spot, find where net re-priced GEX
    # crosses zero nearest to spot (linear interpolation between grid pts).
    flip = None
    grid = [spot * (0.85 + 0.005 * i) for i in range(61)]
    vals = [_net_gex_at(contracts, s) for s in grid]
    crossings = []
    for a in range(len(grid) - 1):
        if vals[a] == 0:
            crossings.append(grid[a])
        elif (vals[a] > 0) != (vals[a + 1] > 0):
            frac = abs(vals[a]) / (abs(vals[a]) + abs(vals[a + 1]))
            crossings.append(grid[a] + frac * (grid[a + 1] - grid[a]))
    if crossings:
        flip = min(crossings, key=lambda x: abs(x - spot))

    top = sorted(per_strike.items(), key=lambda kv: abs(kv[1]),
                 reverse=True)[:10]
    regime = None
    if flip is not None:
        regime = "pinning" if spot >= flip else "slippery"
    elif net:
        regime = "pinning" if net > 0 else "slippery"
    return {
        "ticker": ticker, "spot": round(spot, 2),
        "call_wall": call_wall, "put_wall": put_wall,
        "gamma_flip": round(flip, 2) if flip is not None else None,
        "net_gex_bn": round(net / 1e9, 3),
        "regime": regime,
        "top_strikes": [{"strike": k, "gex_bn": round(v / 1e9, 3)}
                        for k, v in top],
        "contracts": len(contracts),
    }


def run_gex_scan() -> dict:
    """Nightly: indexes + active watchlist names into gex_levels, stamped
    on the completed session's date."""
    import json
    from screen.reversal_screen import _conn
    from analysis.options_picker import iv_session_date
    t0 = time.time()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM watchlist WHERE active = true")
            names = list(INDEXES) + sorted(
                {r[0] for r in cur.fetchall()} - set(INDEXES))
            cur.execute("""
                SELECT t.ticker, d.close
                FROM unnest(%s::text[]) AS t(ticker)
                JOIN LATERAL (
                    SELECT close FROM daily_prices
                    WHERE ticker = t.ticker
                    ORDER BY trade_date DESC LIMIT 1
                ) d ON true
            """, (names,))
            closes = {r[0]: float(r[1]) for r in cur.fetchall() if r[1]}
    finally:
        conn.close()
    as_of = iv_session_date()
    stored, thin = 0, []
    rows = []
    for t in names:
        try:
            g = compute_gex(t, fallback_spot=closes.get(t))
        except Exception as e:
            log.warning(f"[gex] {t} failed: {e}")
            continue
        if not g:
            thin.append(t)
            continue
        rows.append((t, as_of, g["spot"], g["call_wall"], g["put_wall"],
                     g["gamma_flip"], g["net_gex_bn"], g["regime"],
                     json.dumps(g["top_strikes"]), g["contracts"]))
    if rows:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO gex_levels
                        (ticker, as_of, spot, call_wall, put_wall,
                         gamma_flip, net_gex, regime, top_strikes, contracts)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT (ticker, as_of) DO UPDATE SET
                        spot=EXCLUDED.spot, call_wall=EXCLUDED.call_wall,
                        put_wall=EXCLUDED.put_wall,
                        gamma_flip=EXCLUDED.gamma_flip,
                        net_gex=EXCLUDED.net_gex, regime=EXCLUDED.regime,
                        top_strikes=EXCLUDED.top_strikes,
                        contracts=EXCLUDED.contracts,
                        computed_at=now()
                """, rows)
            conn.commit()
            stored = len(rows)
        finally:
            conn.close()
    log.info(f"[gex] stored {stored} names for {as_of} "
             f"({len(thin)} thin/skipped) in {time.time()-t0:.0f}s")
    return {"stored": stored, "as_of": str(as_of), "thin": thin}
