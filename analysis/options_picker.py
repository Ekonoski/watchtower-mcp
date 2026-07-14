"""
Watchtower options contract picker — turns a pattern setup into a ticket.

The system already produces the hard parts of an options decision: entry,
stop, target, measured resolution time (Est DTE, from the pattern timing
backtest), and higher-timeframe context. This module adds the last mile
via Polygon's options snapshot (Options Starter: chains with greeks, IV,
open interest; 15-minute delayed — decision data, not execution data):

  build_ticket(ticker) ->
    - expiry (RUNNER): the nearest expiration at/after the Est-DTE floor
      (2x the p75 full-resolution time — the "buy twice the time you think
      you need" rule), from the pattern's own measured history
    - swing leg: a second, SHORTER expiry sized to the measured time to
      the FIRST TRIM (+1R, r1_p75 x 1.5, >=21 DTE) — the contract for a
      trim-into-strength trade that banks the first push and re-enters,
      rather than holding through the full measured move
    - directional leg: the ~0.65-delta call (bullish) / put (bearish) at
      each expiry, liquidity-gated on open interest
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
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

MIN_OI = 100          # liquidity gate; relaxed to 25 with a warning
TARGET_DELTA = 0.65   # directional leg


def _fetch_chain(ticker: str, contract_type: str, exp_gte: date, exp_lte: date,
                 strike_lo: float, strike_hi: float, limit: int = 250) -> list:
    """Option contract snapshots for one underlying, filtered server-side
    where Polygon allows and client-side for the rest.

    Must use the v3 options-chain endpoint (list_snapshot_options_chain);
    get_snapshot_all("options") builds a v2 URL that Polygon 404s — the
    deploy probe caught that live."""
    from analysis.polygon_data import get_client
    client = get_client()
    if not client:
        return []
    out = []
    try:
        from itertools import islice
        snaps = islice(client.list_snapshot_options_chain(
            ticker,
            params={
                "contract_type": contract_type,
                "expiration_date.gte": exp_gte.isoformat(),
                "expiration_date.lte": exp_lte.isoformat(),
                "strike_price.gte": strike_lo,
                "strike_price.lte": strike_hi,
                "limit": 250,
            },
        ), limit)
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

        from analysis.pattern_backtest import estimate_trim
        stats = timing_stats(conn)
        est = estimate_resolution(pattern, tf, anchor_date, stats) or {}
        dte = int(est.get("dte") or 60)
        # Swing leg: sized to the measured time-to-FIRST-TRIM (+1R), not the
        # full move — the contract for a trim-into-strength trade. Falls back
        # to a third of the runner DTE when the pattern has no +1R sample.
        trim = estimate_trim(pattern, tf, stats) or {}
        swing_dte = int(trim.get("dte") or max(45, dte // 3))
        today = date.today()
        exp_gte = today + timedelta(days=min(max(swing_dte - 4, 10),
                                             max(dte - 7, 14)))
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
        runner_floor = (today + timedelta(days=max(dte - 7, 14))).isoformat()
        swing_floor = (today + timedelta(days=max(swing_dte - 4, 10))).isoformat()
        expiry = next((e for e in exps if e >= runner_floor), exps[-1])
        swing_expiry = next((e for e in exps if e >= swing_floor), exps[0])
        at_exp = [c for c in liquid if c["exp"] == expiry]
        earnings = _earnings_inside(conn, ticker, date.fromisoformat(expiry))

        def _pick_leg(chain_slice):
            # Delta nearest ±TARGET_DELTA (fall back to the strike nearest
            # the trigger when greeks are missing).
            want = TARGET_DELTA if bull else -TARGET_DELTA
            with_delta = [c for c in chain_slice if c["delta"] is not None]
            if with_delta:
                return min(with_delta, key=lambda c: abs(c["delta"] - want))
            return min(chain_slice, key=lambda c: abs(c["strike"] - trigger))

        leg = _pick_leg(at_exp)
        swing = None
        if swing_expiry != expiry:
            swing_at = [c for c in liquid if c["exp"] == swing_expiry]
            if swing_at:
                swing = {"expiry": swing_expiry, "leg": _pick_leg(swing_at),
                         "dte_floor": swing_dte,
                         "weeks_to_trim": trim.get("weeks_hi"),
                         "source": trim.get("source") or "est",
                         # Earnings inside the SWING window is the sharp
                         # edge: IV crush hits short-dated contracts hardest.
                         # Rule: if the pop hasn't arrived by the day before
                         # the print, take what's there or cut — don't drift
                         # through it by default.
                         "er_inside": bool(earnings and
                                           earnings["date"] <= swing_expiry)}

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
        iv_ctx = None
        # Prefer our own IV-rank history once it exists; realized-vol proxy
        # covers the gap while it accumulates.
        rank = iv_rank(conn, ticker)
        if rank:
            r = rank["iv_rank"]
            iv_ctx = {"atm_iv": round(atm["iv"], 3) if atm["iv"] else None,
                      "iv_rank": r, "obs": rank["obs"],
                      "read": ("rich — favor spreads" if r >= 70 else
                               "cheap — straight options fine" if r <= 30 else
                               "fair")}
        else:
            rv = _realized_vol_21d(conn, ticker)
            if atm["iv"] and rv:
                ratio = atm["iv"] / rv
                iv_ctx = {"atm_iv": round(atm["iv"], 3), "realized_21d": round(rv, 3),
                          "ratio": round(ratio, 2),
                          "read": ("rich — favor spreads" if ratio >= 1.3 else
                                   "cheap — straight options fine" if ratio <= 0.9 else
                                   "fair")}

        # First trim = +1R: the trigger's risk unit projected forward
        # (2*trigger - invalid works for both directions). This is the level
        # the swing leg monetizes — 91% of EMA bounces tag it.
        trim_1r = round(2 * trigger - invalid, 2) if invalid is not None else None

        return {
            "ticker": ticker, "pattern": pattern, "timeframe": tf,
            "direction": direction, "status": status, "score": float(score or 0),
            "entry": trigger, "stop": invalid, "trim_1r": trim_1r,
            "target": target, "last_close": last_close,
            "est_weeks": [est.get("weeks_lo"), est.get("weeks_hi")],
            "dte_floor": dte, "expiry": expiry,
            "expiries_available": exps[:4],
            "directional": leg, "swing": swing, "vertical": vertical,
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


# ── IV history + rank (the compounding data asset) ──────────────────────────

def atm_iv_snapshot(ticker: str, close: float):
    """ATM implied volatility + OI totals from a small chain window
    (30-75 DTE, strikes within ~12% of the close). One snapshot call."""
    if not close or close <= 0:
        return None
    today = date.today()
    calls = _fetch_chain(ticker, "call", today + timedelta(days=30),
                         today + timedelta(days=75),
                         close * 0.88, close * 1.12, limit=100)
    puts = _fetch_chain(ticker, "put", today + timedelta(days=30),
                        today + timedelta(days=75),
                        close * 0.88, close * 1.12, limit=100)
    if not calls and not puts:
        return None
    ivs = []
    for side in (calls, puts):
        with_iv = [c for c in side if c["iv"]]
        if with_iv:
            atm = min(with_iv, key=lambda c: abs(c["strike"] - close))
            ivs.append(atm["iv"])
    if not ivs:
        return None
    return {
        "atm_iv": round(sum(ivs) / len(ivs), 4),
        "call_oi": sum(c["oi"] or 0 for c in calls),
        "put_oi": sum(c["oi"] or 0 for c in puts),
    }


def iv_session_date() -> date:
    """The completed session an after-hours IV snapshot belongs to.
    Options don't trade overnight, so from the 4 PM close until the next
    open the chain still shows that session's closing quotes — shifting
    ET back 16 hours maps any time in that window onto the session's
    date. (The old CURRENT_DATE stamp was UTC: after 8 PM ET it rolled
    to tomorrow and would have labeled tonight's IV with the wrong day.)"""
    from zoneinfo import ZoneInfo
    et = datetime.now(ZoneInfo("America/New_York"))
    return (et - timedelta(hours=16)).date()


def run_iv_snapshot(top_n: int = 500) -> dict:
    """Nightly: store ATM IV + OI for every name with a live pattern (by
    score, bounded) plus the watchlist. This is how Watchtower grows its
    own IV-rank history — after ~3 months, iv_rank() answers 'is premium
    rich or cheap for THIS name' from proprietary data."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker FROM (
                    SELECT DISTINCT ON (ticker) ticker, score
                    FROM pattern_scan ORDER BY ticker, score DESC
                ) p ORDER BY score DESC NULLS LAST LIMIT %s
            """, (top_n,))
            names = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT ticker FROM watchlist WHERE active = true")
            names |= {r[0] for r in cur.fetchall()}
            # Latest close per name via bounded index probes (LATERAL
            # top-1). The old DISTINCT ON walked and sorted every stored
            # bar for the whole universe and blew the 120s statement
            # timeout under evening load — killing the run before a
            # single chain was fetched.
            cur.execute("""
                SELECT t.ticker, d.close
                FROM unnest(%s::text[]) AS t(ticker)
                JOIN LATERAL (
                    SELECT close FROM daily_prices
                    WHERE ticker = t.ticker
                    ORDER BY trade_date DESC LIMIT 1
                ) d ON true
            """, (sorted(names),))
            closes = dict(cur.fetchall())
    finally:
        conn.close()
    log.info(f"[options] IV snapshot over {len(closes)} names")

    def _one(t):
        snap = atm_iv_snapshot(t, float(closes[t]))
        return (t, snap) if snap else None

    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_one, t): t for t in closes}
        for f in as_completed(futs):
            try:
                r = f.result()
                if r:
                    rows.append(r)
            except Exception:
                pass
    # Fresh connection for the write: the chain fetches take minutes, and
    # a connection held idle across that window is exactly the kind the
    # pooler reaps — which would lose the night's snapshots at the last
    # step. Setup conn is closed above; this one lives only for the write.
    if rows:
        as_of = iv_session_date()
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO iv_history (ticker, as_of, atm_iv, call_oi, put_oi)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, as_of) DO UPDATE SET
                        atm_iv = EXCLUDED.atm_iv, call_oi = EXCLUDED.call_oi,
                        put_oi = EXCLUDED.put_oi
                """, [(t, as_of, s["atm_iv"], s["call_oi"], s["put_oi"])
                      for t, s in rows])
            conn.commit()
        finally:
            conn.close()
    log.info(f"[options] IV snapshot stored {len(rows)} names")
    return {"stored": len(rows), "universe": len(closes)}


def iv_rank(conn, ticker: str):
    """Percentile of today's ATM IV within this name's trailing year of
    our own snapshots. None until ~20 observations exist — the realized-vol
    proxy covers the gap while history accumulates."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT atm_iv FROM iv_history
                WHERE ticker = %s AND as_of >= CURRENT_DATE - 370
                ORDER BY as_of
            """, (ticker,))
            ivs = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
        if len(ivs) < 20:
            return None
        cur_iv = ivs[-1]
        rank = sum(1 for v in ivs if v <= cur_iv) / len(ivs) * 100
        return {"iv_rank": round(rank), "obs": len(ivs), "atm_iv": cur_iv}
    except Exception:
        return None


def ticket_one_liner(ticker: str) -> str:
    """Compact ticket for alert emails: '→ Opt: Sep18 $35C ~0.63Δ (OI 2.5k) /
    vert 35-42 ~$1.85'. Empty string on any problem — alerts never break
    because options data hiccuped."""
    try:
        t = build_ticket(ticker)
        if not t or t.get("error"):
            return ""
        d = t["directional"]
        cp = "C" if t["direction"] == "bullish" else "P"
        exp = t["expiry"][5:].replace("-", "/")     # MM/DD
        s = "→ Opt:"
        sw = t.get("swing")
        if sw and sw.get("leg"):
            sl = sw["leg"]
            s += f" swing {sw['expiry'][5:].replace('-', '/')} ${sl['strike']:g}{cp}"
            if sl.get("delta") is not None:
                s += f" ~{abs(sl['delta']):.2f}Δ"
            s += " /"
        s += f" runner {exp} ${d['strike']:g}{cp}"
        if d.get("delta") is not None:
            s += f" ~{abs(d['delta']):.2f}Δ"
        if d.get("oi"):
            s += f" (OI {d['oi']:,})"
        v = t.get("vertical")
        if v and v.get("est_debit"):
            s += (f" / vert {v['long']['strike']:g}-{v['short']['strike']:g}"
                  f" ~${v['est_debit']}")
        if t.get("earnings"):
            s += f" ⚠ER {t['earnings']['date'][5:]}"
        return s
    except Exception:
        return ""
