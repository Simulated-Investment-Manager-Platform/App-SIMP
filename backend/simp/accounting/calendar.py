"""NYSE session calendar, S&P quarterly effective dates and US settlement cycles.

In live mode the session list comes from the CRSP release (``dsf_v2`` dates), as the handoff
did. The rule-based calendar below generates the same sessions for the synthetic source and
is cross-checked against CRSP dates when both exist.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

T1_CUTOVER = date(2024, 5, 28)  # US equities moved from T+2 to T+1 on 2024-05-28

# One-off full-day closures (national days of mourning).
SPECIAL_CLOSURES = {date(2025, 1, 9), date(2018, 12, 5)}


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    hol = {
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    ny = date(year, 1, 1)
    if ny.weekday() != 5:  # NYSE does not observe New Year on the prior Friday
        hol.add(_observed(ny))
    if year >= 2022:
        hol.add(_observed(date(year, 6, 19)))
    return hol


def nyse_sessions(start: date, end: date) -> list[date]:
    hols: set[date] = set()
    for y in range(start.year, end.year + 1):
        hols |= nyse_holidays(y)
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in hols and d not in SPECIAL_CLOSURES:
            out.append(d)
        d += timedelta(days=1)
    return out


class SessionCalendar:
    """Index arithmetic over an ordered list of sessions."""

    def __init__(self, sessions: Sequence[date]) -> None:
        self.sessions = list(sessions)
        self._pos = {d: i for i, d in enumerate(self.sessions)}

    def __len__(self) -> int:
        return len(self.sessions)

    def index(self, d: date) -> int:
        return self._pos[d]

    def is_session(self, d: date) -> bool:
        return d in self._pos

    def offset(self, d: date, n: int) -> date:
        return self.sessions[self._pos[d] + n]

    def on_or_before(self, d: date) -> date:
        i = bisect_right(self.sessions, d) - 1
        if i < 0:
            raise KeyError(d)
        return self.sessions[i]

    def on_or_after(self, d: date) -> date:
        i = bisect_left(self.sessions, d)
        if i >= len(self.sessions):
            raise KeyError(d)
        return self.sessions[i]

    def between(self, a: date, b: date) -> list[date]:
        return self.sessions[bisect_left(self.sessions, a) : bisect_right(self.sessions, b)]

    def settlement_date(self, trade_date: date) -> date:
        lag = 1 if trade_date >= T1_CUTOVER else 2
        return self.offset(trade_date, lag)


def third_friday(year: int, month: int) -> date:
    return _nth_weekday(year, month, 4, 3)


def quarterly_effective_dates(cal: SessionCalendar, start: date, end: date) -> list[date]:
    """Session whose close an S&P quarterly rebalance takes effect at.

    Changes take effect before the open on the Monday after the third Friday of
    Mar/Jun/Sep/Dec, so index funds trade the third Friday's closing auction. If that Friday
    is not a session (e.g. Juneteenth 2026-06-19) the prior session is used.
    """
    out = []
    for y in range(start.year, end.year + 1):
        for m in (3, 6, 9, 12):
            tf = third_friday(y, m)
            if start <= tf <= end:
                out.append(cal.on_or_before(tf))
    return out


def ny_ts(d: date, t: time) -> datetime:
    """Local New York wall time -> aware datetime (stored as UTC by the DB layer)."""
    return datetime.combine(d, t, tzinfo=NY)


def close_ts(d: date) -> datetime:
    return ny_ts(d, time(16, 0))


def open_ts(d: date) -> datetime:
    return ny_ts(d, time(9, 30))
