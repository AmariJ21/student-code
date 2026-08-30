"""
Market data provider abstraction. Any provider (Yahoo/yfinance-style, Polygon,
etc.) implements get_bars() and returns a common Bar/BarSeries shape so the
backtester never has to know which provider supplied a given trade's prices.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    ts: dt.datetime  # tz-aware
    open: float
    high: float
    low: float
    close: float
    adj_close: float | None
    volume: float | None


@dataclass
class BarSeries:
    ticker: str
    interval: str  # "1m" / "5m" / "1d"
    bars: list[Bar]
    source: str


class MarketDataProvider(ABC):
    @abstractmethod
    def get_bars(self, ticker: str, start: dt.datetime, end: dt.datetime, interval: str) -> BarSeries:
        """Return whatever bars this provider can actually supply for [start, end].

        Implementations MUST NOT fabricate bars for a range they don't have data
        for -- return an empty BarSeries rather than interpolate or guess.
        """
        raise NotImplementedError

    @abstractmethod
    def max_interval_lookback(self, interval: str, as_of: dt.datetime) -> dt.datetime | None:
        """Earliest timestamp this provider can serve at this interval, given
        `as_of` "now". Returns None if there is effectively no limit (e.g. daily).
        Used by the resolution fallback chain in market_data/resolution.py.
        """
        raise NotImplementedError
