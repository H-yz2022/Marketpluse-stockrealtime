"""Trading sessions and the Beijing / Hong Kong clock.

Both markets run on UTC+8 with no daylight saving, so a fixed offset is exact.
Public holidays are not hard-coded: callers detect them from quote timestamps
(a quote whose date is not today during session hours means the market is shut).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

BJ = timezone(timedelta(hours=8), name="Asia/Shanghai")

# (start, end) of each continuous-trading block.
SESSIONS = {
    "CN": [(time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))],
    "HK": [(time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))],
}
PRE_OPEN = {"CN": time(9, 15), "HK": time(9, 0)}
CLOSE_AUCTION_END = {"CN": time(15, 0), "HK": time(16, 10)}


def now_bj() -> datetime:
    return datetime.now(BJ)


def total_minutes(mkt: str) -> int:
    return sum(_minutes(a, b) for a, b in SESSIONS[mkt])  # CN 240, HK 330


def _minutes(a: time, b: time) -> int:
    return (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)


@dataclass(frozen=True)
class SessionStatus:
    market: str
    phase: str          # pre-open | morning | lunch | afternoon | closing auction | closed | weekend
    is_trading: bool
    elapsed_min: int    # continuous-trading minutes elapsed today (0..total)
    total_min: int
    to_close_min: int   # minutes until the final close, 0 when closed

    @property
    def fraction(self) -> float:
        return self.elapsed_min / self.total_min if self.total_min else 0.0

    @property
    def label(self) -> str:
        if self.phase in ("morning", "afternoon"):
            return f"{self.phase.capitalize()} session · {self.to_close_min} min to close"
        return self.phase.capitalize()


def status(mkt: str, at: datetime | None = None) -> SessionStatus:
    """Session phase at `at` (Beijing time). Weekends are detected; holidays are not."""
    at = (at or now_bj()).astimezone(BJ)
    total = total_minutes(mkt)
    if at.weekday() >= 5:
        return SessionStatus(mkt, "weekend", False, total, total, 0)
    t = at.time()
    (am_open, am_close), (pm_open, pm_close) = SESSIONS[mkt]
    am_len = _minutes(am_open, am_close)
    close_min = pm_close.hour * 60 + pm_close.minute
    now_min = t.hour * 60 + t.minute
    if t < PRE_OPEN[mkt]:
        return SessionStatus(mkt, "closed", False, 0, total, 0)
    if t < am_open:
        return SessionStatus(mkt, "pre-open", False, 0, total, close_min - now_min)
    if t < am_close:
        el = _minutes(am_open, t)
        return SessionStatus(mkt, "morning", True, el, total, close_min - now_min)
    if t < pm_open:
        return SessionStatus(mkt, "lunch", False, am_len, total, close_min - now_min)
    if t < pm_close:
        el = am_len + _minutes(pm_open, t)
        return SessionStatus(mkt, "afternoon", True, el, total, close_min - now_min)
    if t < CLOSE_AUCTION_END[mkt]:
        return SessionStatus(mkt, "closing auction", False, total, total, 0)
    return SessionStatus(mkt, "closed", False, total, total, 0)


def elapsed_at(mkt: str, hhmm: str) -> int:
    """Continuous-trading minutes elapsed at a clock time such as '14:00'."""
    h, m = (int(x) for x in hhmm.split(":"))
    at = datetime.combine(date(2024, 1, 3), time(h, m), BJ)  # any weekday
    return status(mkt, at).elapsed_min


def session_of(mkt: str, t: time) -> str:
    """'AM', 'PM', 'auction' (pre-open) or 'post' for a minute-bar timestamp."""
    (am_open, am_close), (pm_open, pm_close) = SESSIONS[mkt]
    if t < am_open:
        return "auction"
    if t <= am_close:
        return "AM"
    if pm_open <= t <= pm_close:
        return "PM"
    if t > pm_close:
        return "post"
    return "AM"
