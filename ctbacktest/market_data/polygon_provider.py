"""
Optional paid provider for deeper intraday history than Yahoo can serve for
free (see FEASIBILITY.md #7: Polygon.io's free tier gives 2 years of history;
paid tiers extend to 5/10/20+ years). Off by default -- requires the user to
set POLYGON_API_KEY themselves. Never assumed to be configured; callers that
don't set the key simply don't get this provider (fall back to Yahoo/daily).
"""

from __future__ import annotations

import datetime as dt
import logging
import os

import requests

from ctbacktest.market_data.base import Bar, BarSeries, MarketDataProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"

_MULTIPLIER_TIMESPAN = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "1d": (1, "day"),
}


class PolygonNotConfigured(RuntimeError):
    pass


class PolygonProvider(MarketDataProvider):
    def __init__(self, api_key: str | None = None, timeout: int = 20):
        self.api_key = api_key or os.environ.get("POLYGON_API_KEY")
        if not self.api_key:
            raise PolygonNotConfigured("POLYGON_API_KEY is not set; Polygon is an optional paid provider (see .env.example).")
        self.timeout = timeout
        self.session = requests.Session()

    def max_interval_lookback(self, interval: str, as_of: dt.datetime) -> dt.datetime | None:
        # Depends on the user's actual Polygon plan; we don't know it here, so
        # we don't claim a limit -- a request outside the plan's coverage will
        # simply come back empty from get_bars() rather than being pre-filtered.
        return None

    def get_bars(self, ticker: str, start: dt.datetime, end: dt.datetime, interval: str) -> BarSeries:
        if interval not in _MULTIPLIER_TIMESPAN:
            raise ValueError(f"Unsupported interval for Polygon provider: {interval}")
        multiplier, timespan = _MULTIPLIER_TIMESPAN[interval]
        url = (
            f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/"
            f"{start.date().isoformat()}/{end.date().isoformat()}"
        )
        try:
            resp = self.session.get(
                url,
                params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            logger.exception("Polygon fetch failed for %s [%s, %s] interval=%s", ticker, start, end, interval)
            return BarSeries(ticker=ticker, interval=interval, bars=[], source="polygon")

        results = payload.get("results") or []
        bars = [
            Bar(
                ts=dt.datetime.fromtimestamp(r["t"] / 1000, tz=dt.timezone.utc),
                open=r["o"],
                high=r["h"],
                low=r["l"],
                close=r["c"],
                adj_close=r["c"],  # Polygon's "adjusted=true" already applies split adjustment to OHLC
                volume=r.get("v"),
            )
            for r in results
        ]
        return BarSeries(ticker=ticker, interval=interval, bars=bars, source="polygon")
