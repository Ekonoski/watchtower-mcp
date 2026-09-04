"""
watchtower_bars — a live read of a ticker's intraday tape from the
real-time Polygon feed (2026-09-04, Eric at 9:31: "can't we just pull
them from polygon?"). The server can; a chat session cannot (no key,
no egress), so this tool is the window: today's (or a given day's)
1m / 5m / 15m bars, RTH by default, rendered compactly with the day's
open/high/low/last, the 9:30 bar called out, and the last N bars.

Read-only: it fetches and formats, writes nothing. It is NOT a decision
surface — the books decide on their own persisted bars (reconstruction
is not tape); this is for a human asking "what printed at 9:30?".
"""
import datetime as dt
from typing import List, Optional, Tuple

RTH_START, RTH_END = dt.time(9, 30), dt.time(15, 59)
MULT = {"1m": 1, "5m": 5, "15m": 15}


def _px(v):
    return f"{v:.2f}"


def format_bars(ticker: str, day: dt.date, tf: str,
                bars: List[Tuple[dt.datetime, float, float, float, float, Optional[float]]],
                last_n: int = 12, note: str = "") -> str:
    """Pure. bars = [(ts_et, o, h, l, c, v)] ascending, already filtered
    to the session wanted. Zero bars is stated, never rendered as a
    quiet tape."""
    if not bars:
        return (f"{ticker} {tf} bars for {day}: none available"
                f"{' — ' + note if note else ''}. A hole, not a flat session.")
    o = bars[0][1]
    hi = max(b[2] for b in bars)
    lo = min(b[3] for b in bars)
    last = bars[-1]
    lines = [f"**{ticker}** {tf} bars · {day} · {len(bars)} bars through "
             f"{last[0].strftime('%H:%M')} ET"
             f"{' · ' + note if note else ''}",
             f"Open {_px(o)} · High {_px(hi)} · Low {_px(lo)} · Last {_px(last[4])} "
             f"({(last[4] / o - 1) * 1e4:+.0f} bps from the open)"]
    first = next((b for b in bars if b[0].time() == RTH_START), None)
    if first is not None:
        lines.append(f"9:30 bar: O {_px(first[1])} H {_px(first[2])} "
                     f"L {_px(first[3])} C {_px(first[4])}")
    lines.append("")
    lines.append("time   open    high    low     close   vol")
    shown = bars[-last_n:]
    if len(bars) > last_n:
        lines.append(f"… {len(bars) - last_n} earlier bars not shown (count stated)")
    for ts, bo, bh, bl, bc, bv in shown:
        vol = f"{int(bv):,}" if bv is not None else "—"
        lines.append(f"{ts.strftime('%H:%M')}  {_px(bo):>7} {_px(bh):>7} "
                     f"{_px(bl):>7} {_px(bc):>7}  {vol}")
    return "\n".join(lines)


def fetch_bars(ticker: str, day: dt.date, tf: str, include_premarket: bool = False):
    """Polygon aggs for one day, converted to ET tuples. Returns (bars,
    note): note names the reason when bars are empty."""
    from zoneinfo import ZoneInfo

    from analysis.polygon_data import get_client
    et = ZoneInfo("America/New_York")
    client = get_client()
    if client is None:
        return [], "no Polygon client on this server"
    try:
        aggs = list(client.get_aggs(ticker, multiplier=MULT[tf], timespan="minute",
                                    from_=day.isoformat(), to=day.isoformat(),
                                    limit=5000))
    except Exception as e:
        return [], f"fetch failed: {type(e).__name__}: {str(e)[:300]}"
    out = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000, dt.timezone.utc).astimezone(et)
        if not include_premarket and not (RTH_START <= t.time() <= RTH_END):
            continue
        out.append((t, float(a.open), float(a.high), float(a.low), float(a.close),
                    float(a.volume) if a.volume is not None else None))
    return out, ""


def bars_report(ticker: str, timeframe: str = "1m", day: str = "",
                last_n: int = 12, include_premarket: bool = False) -> str:
    from zoneinfo import ZoneInfo
    ticker = (ticker or "").strip().upper()
    tf = (timeframe or "1m").strip().lower()
    if tf not in MULT:
        return f"timeframe must be one of {', '.join(MULT)}"
    if not ticker:
        return "ticker is required"
    if day:
        d = dt.date.fromisoformat(day.strip())
    else:
        d = dt.datetime.now(ZoneInfo("America/New_York")).date()
    bars, note = fetch_bars(ticker, d, tf, include_premarket)
    if include_premarket:
        note = (note + " · " if note else "") + "premarket included"
    return format_bars(ticker, d, tf, bars, last_n=max(1, min(int(last_n), 120)), note=note)
