"""Trading sessions and the Beijing / Hong Kong clock.

Both markets run on UTC+8 with no daylight saving, so a fixed offset is exact.
Trading days come from the exchanges' official calendars (stockrt.tradingdays).
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
    phase: str          # before open | pre-open | morning | lunch | afternoon | closing auction | closed
    #                     | weekend | holiday
    is_trading: bool
    elapsed_min: int    # continuous-trading minutes elapsed today (0..total)
    total_min: int
    to_close_min: int   # minutes until the final close, 0 when closed
    holiday: str | None = None
    next_session: date | None = None
    half_day: bool = False
    to_open_min: int = 0

    @property
    def fraction(self) -> float:
        return self.elapsed_min / self.total_min if self.total_min else 0.0

    @property
    def label(self) -> str:
        nxt = f"{self.next_session:%a %d %b} 09:30" if self.next_session else None
        if self.phase in ("weekend", "holiday"):
            why = "weekend" if self.phase == "weekend" else (self.holiday or "market holiday")
            return f"Closed · {why}" + (f" · reopens {nxt}" if nxt else "")
        if self.phase == "before open":
            return f"Opens 09:30 today · in {_dur(self.to_open_min)}"
        if self.phase == "pre-open":
            return f"Pre-open auction · opens 09:30 in {_dur(self.to_open_min)}"
        if self.phase in ("morning", "afternoon"):
            half = " (half day)" if self.half_day else ""
            return f"Open · {self.phase} session{half} · {_dur(self.to_close_min)} to close"
        if self.phase == "lunch":
            return "Lunch break · resumes 13:00"
        if self.phase == "closing auction":
            return "Closing auction"
        return "Closed for today" + (f" · next session {nxt}" if nxt else "")


def _dur(minutes: int) -> str:
    h, m = divmod(max(minutes, 0), 60)
    return f"{h} h {m} min" if h else f"{m} min"


def clock_status(mkt: str, at: datetime | None = None, half_day: bool = False) -> SessionStatus:
    """Session phase from the clock alone (weekdays assumed open). Never touches the network."""
    at = (at or now_bj()).astimezone(BJ)
    total = total_minutes(mkt)
    t = at.time()
    (am_open, am_close), (pm_open, pm_close) = SESSIONS[mkt]
    if half_day:
        pm_open = pm_close = am_close
        total = _minutes(am_open, am_close)
    am_len = _minutes(am_open, am_close)
    close_min = pm_close.hour * 60 + pm_close.minute
    now_min = t.hour * 60 + t.minute
    to_open = am_open.hour * 60 + am_open.minute - now_min
    kw = {"half_day": half_day}
    if t < PRE_OPEN[mkt]:
        return SessionStatus(mkt, "before open", False, 0, total, 0, to_open_min=to_open, **kw)
    if t < am_open:
        return SessionStatus(mkt, "pre-open", False, 0, total, close_min - now_min, to_open_min=to_open, **kw)
    if t < am_close:
        el = _minutes(am_open, t)
        return SessionStatus(mkt, "morning", True, el, total, close_min - now_min, **kw)
    if t < pm_open:
        return SessionStatus(mkt, "lunch", False, am_len, total, close_min - now_min, **kw)
    if t < pm_close:
        el = am_len + _minutes(pm_open, t)
        return SessionStatus(mkt, "afternoon", True, el, total, close_min - now_min, **kw)
    auction_end = time(12, 10) if half_day else CLOSE_AUCTION_END[mkt]
    if t < auction_end:
        return SessionStatus(mkt, "closing auction", False, total, total, 0, **kw)
    return SessionStatus(mkt, "closed", False, total, total, 0, **kw)


def status(mkt: str, at: datetime | None = None) -> SessionStatus:
    """Session phase at `at` (Beijing time), using the exchanges' official trading calendars:
    weekends, public holidays and HK half days are all handled."""
    from . import tradingdays  # local import: tradingdays -> http -> (no calendar) keeps imports acyclic

    at = (at or now_bj()).astimezone(BJ)
    day = at.date()
    trading = tradingdays.is_trading_day(mkt, day)
    nxt = tradingdays.next_trading_day(mkt, day)
    if day.weekday() >= 5:
        total = total_minutes(mkt)
        return SessionStatus(mkt, "weekend", False, total, total, 0, next_session=nxt)
    if trading is False:
        total = total_minutes(mkt)
        return SessionStatus(mkt, "holiday", False, total, total, 0, holiday=tradingdays.holiday_name(mkt, day),
                             next_session=nxt)
    s = clock_status(mkt, at, half_day=tradingdays.half_day(mkt, day))
    if s.phase == "closed":
        return SessionStatus(**{**s.__dict__, "next_session": nxt})
    return s


def elapsed_at(mkt: str, hhmm: str) -> int:
    """Continuous-trading minutes elapsed at a clock time such as '14:00'."""
    h, m = (int(x) for x in hhmm.split(":"))
    at = datetime.combine(date(2024, 1, 3), time(h, m), BJ)  # any weekday
    return clock_status(mkt, at).elapsed_min


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
