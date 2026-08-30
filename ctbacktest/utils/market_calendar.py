"""
NYSE trading-calendar helper built on `pandas_market_calendars` (a
well-maintained, holiday-aware calendar library) so "next market open" isn't
a hand-rolled weekday-only approximation that would misfire around holidays.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

_NYSE = mcal.get_calendar("NYSE")


@lru_cache(maxsize=8)
def _schedule(start_year: int, end_year: int) -> pd.DataFrame:
    return _NYSE.schedule(start_date=f"{start_year}-01-01", end_date=f"{end_year}-12-31")


def _ensure_utc(ts: dt.datetime) -> pd.Timestamp:
    p = pd.Timestamp(ts)
    return p.tz_localize("UTC") if p.tzinfo is None else p.tz_convert("UTC")


def earliest_tradable_timestamp(ts: dt.datetime) -> dt.datetime:
    """The earliest moment at/after `ts` when the market is actually open:
    `ts` unchanged if it falls inside a live session (the market is already
    open -- no need to wait for that day's open), otherwise the next
    session's open. This is deliberately NOT "roll back to that day's 9:30am
    open" when `ts` is mid-session, which would incorrectly discard hours of
    same-day tradability for a Senate filing with a real afternoon timestamp."""
    p = _ensure_utc(ts)
    sched = _schedule(p.year - 1, p.year + 1)
    opens = sched["market_open"]
    closes = sched["market_close"]
    idx = opens.searchsorted(p, side="left")
    if idx > 0 and opens.iloc[idx - 1] <= p <= closes.iloc[idx - 1]:
        return p.to_pydatetime()
    if idx >= len(opens):
        raise ValueError(f"No NYSE session found on/after {ts}; calendar may need a wider range.")
    return opens.iloc[idx].to_pydatetime()


def next_market_open_at_or_after(ts: dt.datetime) -> dt.datetime:
    """The open of the next full NYSE session at/after `ts` (rolls forward
    even if `ts` is mid-session). Used when we specifically want a session's
    opening print, e.g. for a disclosure with only a date, not a time."""
    p = _ensure_utc(ts)
    sched = _schedule(p.year - 1, p.year + 1)
    opens = sched["market_open"]
    idx = opens.searchsorted(p, side="left")
    if idx >= len(opens):
        raise ValueError(f"No NYSE session found on/after {ts}; calendar may need a wider range.")
    return opens.iloc[idx].to_pydatetime()


def is_market_open_at(ts: dt.datetime) -> bool:
    p = _ensure_utc(ts)
    sched = _schedule(p.year - 1, p.year + 1)
    mask = (sched["market_open"] <= p) & (p <= sched["market_close"])
    return bool(mask.any())


def add_trading_days(ts: dt.datetime, n_days: int) -> dt.datetime:
    """`ts` shifted forward by n_days *trading* days (used for max_hold_days)."""
    p = _ensure_utc(ts)
    sched = _schedule(p.year - 1, p.year + 2)
    session_opens = sched["market_open"]
    idx = session_opens.searchsorted(p, side="left")
    target_idx = idx + n_days
    if target_idx >= len(session_opens):
        # fall back to a calendar-day approximation past the end of our cached window
        return (p + pd.Timedelta(days=int(n_days * 1.5))).to_pydatetime()
    return sched["market_close"].iloc[target_idx].to_pydatetime()
