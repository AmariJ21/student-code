"""
Implements the spec's data-resolution hierarchy (tick > 1m > 5m > daily) as a
real fallback chain: for a given entry timestamp, ask each provider/interval
in preference order whether it can actually cover that date, and use the
finest resolution that's genuinely available -- never daily-only by default
just because it's simplest, and never a finer resolution than the provider
can prove it has (see MarketDataProvider.max_interval_lookback).
"""

from __future__ import annotations

import datetime as dt

from ctbacktest.config import PriceResolution
from ctbacktest.market_data.base import BarSeries, MarketDataProvider

# Ordered finest-to-coarsest. "tick" is intentionally omitted -- no provider
# in this project implements it (see FEASIBILITY.md #7: no free tick source exists).
_PREFERENCE_ORDER: list[tuple[str, PriceResolution]] = [
    ("1m", PriceResolution.ONE_MIN),
    ("5m", PriceResolution.FIVE_MIN),
    ("1d", PriceResolution.DAILY),
]


def best_available_series(
    provider: MarketDataProvider,
    ticker: str,
    start: dt.datetime,
    end: dt.datetime,
    now: dt.datetime | None = None,
) -> tuple[BarSeries, PriceResolution]:
    now = now or dt.datetime.now(dt.timezone.utc)
    for interval, resolution in _PREFERENCE_ORDER:
        lookback_floor = provider.max_interval_lookback(interval, now)
        if lookback_floor is not None and start < lookback_floor:
            continue  # provider has told us it can't possibly have data this old at this resolution
        series = provider.get_bars(ticker, start, end, interval)
        if series.bars:
            return series, resolution
    # Nothing at all -- caller must treat this as EXCLUDED_NO_PRICE_DATA, never fabricate.
    return BarSeries(ticker=ticker, interval="1d", bars=[], source=provider.__class__.__name__), PriceResolution.DAILY
