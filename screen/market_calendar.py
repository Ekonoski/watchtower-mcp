"""
US equity market calendar — weekends + full-closure holidays.

Dependency-free (stdlib only). Used to gate scans so Watchtower doesn't burn
paid data-API calls (Polygon / FMP / Grok) when the market is closed, and so
the dashboard clock doesn't claim "market open" on a weekend or holiday.

Covers the 10 NYSE/Nasdaq full-closure holidays, including Good Friday (via the
Easter computus) and weekend-observed shifts. This is the *market* calendar, not
the federal one — e.g. Columbus Day and Veterans Day are federal holidays but
the market is open, while Good Friday is a market holiday but not federal.

NOTE: half-day early closes (1:00 PM ET, ~3 days/yr) are NOT modeled — those are
still trading days; we just scan the normal window. Can add later if desired.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    _ET = None


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous/Meeus computus)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th given weekday (Mon=0) of a month. e.g. 3rd Monday = _nth_weekday(y,1,0,3)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last given weekday (Mon=0) of a month."""
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """Federal/NYSE observed shift: Sat -> preceding Fri, Sun -> following Mon."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def market_holidays(year: int) -> set:
    """Set of full-closure market holiday dates for the given year."""
    h: set = set()

    # New Year's Day. NYSE does NOT close the preceding Friday (Dec 31) when
    # Jan 1 falls on a Saturday; a Sunday shifts to Monday.
    nyd = date(year, 1, 1)
    if nyd.weekday() == 6:
        h.add(date(year, 1, 2))
    elif nyd.weekday() != 5:
        h.add(nyd)

    h.add(_nth_weekday(year, 1, 0, 3))       # MLK Jr. Day — 3rd Mon Jan
    h.add(_nth_weekday(year, 2, 0, 3))       # Washington's Birthday — 3rd Mon Feb
    h.add(_easter(year) - timedelta(days=2)) # Good Friday
    h.add(_last_weekday(year, 5, 0))         # Memorial Day — last Mon May
    h.add(_observed(date(year, 6, 19)))      # Juneteenth
    h.add(_observed(date(year, 7, 4)))       # Independence Day
    h.add(_nth_weekday(year, 9, 0, 1))       # Labor Day — 1st Mon Sep
    h.add(_nth_weekday(year, 11, 3, 4))      # Thanksgiving — 4th Thu Nov
    h.add(_observed(date(year, 12, 25)))     # Christmas Day
    return h


def et_now() -> datetime:
    """Current time in US Eastern (falls back to UTC if zoneinfo is unavailable)."""
    if _ET is not None:
        return datetime.now(_ET)
    return datetime.utcnow()


def is_trading_day(d=None) -> bool:
    """
    True if the US equity market is open on date `d` (a date or datetime;
    defaults to 'today' in ET). False on weekends and full-closure holidays.
    """
    if d is None:
        d = et_now()
    if isinstance(d, datetime):
        d = d.date()
    if d.weekday() >= 5:  # Saturday / Sunday
        return False
    return d not in market_holidays(d.year)


def market_minutes(now: datetime = None) -> tuple:
    """
    (minutes_since_open capped at 390, is_market_hours) — calendar + clock aware.
    is_market_hours is False on weekends/holidays regardless of time of day.
    Single source of truth for "is the market open right now".
    """
    now = now or et_now()
    if not is_trading_day(now):
        return 0, False
    open_m, close_m = 9 * 60 + 30, 16 * 60       # 9:30 → 16:00 ET
    cur = now.hour * 60 + now.minute
    is_open = open_m <= cur <= close_m
    if cur < open_m:
        elapsed = 0
    elif cur > close_m:
        elapsed = 390
    else:
        elapsed = cur - open_m
    return elapsed, is_open
