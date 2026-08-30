"""
Free Yahoo Finance provider. Hits the public chart JSON endpoint directly via
`requests` (verified live against query1.finance.yahoo.com while building
this module) rather than depending on the `yfinance` package's bundled
curl_cffi HTTP client, which does its own TLS stack and can fail in
proxied/sandboxed environments that the plain `requests` path handles fine.
This endpoint is the same one `yfinance` itself calls under the hood.

Resolution limits (see FEASIBILITY.md #7, verified against public Yahoo
documentation/behavior, not assumed):
  - "1d" (and coarser): effectively unlimited history for a surviving ticker.
  - "5m"/"15m"/"30m"/"60m"/"90m"/"1h": only the trailing ~60 days from "now".
  - "1m": only the trailing ~7 days from "now".
Requesting outside these windows returns whatever Yahoo actually has (often
empty) rather than an error; get_bars() returns an empty BarSeries in that
case -- it never fabricates bars.
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

from ctbacktest.market_data.base import Bar, BarSeries, MarketDataProvider

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ctbacktest-research/0.1)"}

_INTRADAY_LOOKBACK_DAYS = {
    "1m": 7,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "60m": 60,
    "1h": 60,
    "90m": 60,
}


class YFinanceProvider(MarketDataProvider):
    def __init__(self, session: requests.Session | None = None, timeout: int = 20):
        self.session = session or requests.Session()
        self.timeout = timeout

    def max_interval_lookback(self, interval: str, as_of: dt.datetime) -> dt.datetime | None:
        days = _INTRADAY_LOOKBACK_DAYS.get(interval)
        if days is None:
            return None  # daily+: no meaningful limit for this project's purposes
        return as_of - dt.timedelta(days=days)

    def get_bars(self, ticker: str, start: dt.datetime, end: dt.datetime, interval: str) -> BarSeries:
        params = {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": interval,
            "includeAdjustedClose": "true",
        }
        try:
            resp = self.session.get(
                CHART_URL.format(ticker=ticker), params=params, headers=_HEADERS, timeout=self.timeout
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            logger.exception("Yahoo chart fetch failed for %s [%s, %s] interval=%s", ticker, start, end, interval)
            return BarSeries(ticker=ticker, interval=interval, bars=[], source="yfinance")

        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            error = (payload.get("chart") or {}).get("error")
            if error:
                logger.warning("Yahoo chart error for %s: %s", ticker, error)
            return BarSeries(ticker=ticker, interval=interval, bars=[], source="yfinance")

        node = result[0]
        timestamps = node.get("timestamp") or []
        quote = (node.get("indicators", {}).get("quote") or [{}])[0]
        adjclose = (node.get("indicators", {}).get("adjclose") or [{}])
        adjclose = adjclose[0].get("adjclose") if adjclose else None

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[Bar] = []
        for i, ts in enumerate(timestamps):
            o, h, l, c = opens[i] if i < len(opens) else None, highs[i] if i < len(highs) else None, \
                lows[i] if i < len(lows) else None, closes[i] if i < len(closes) else None
            if o is None or h is None or l is None or c is None:
                continue  # Yahoo returns nulls for missing bars (e.g. halts); never interpolate
            a = adjclose[i] if adjclose and i < len(adjclose) else None
            v = volumes[i] if i < len(volumes) else None
            bars.append(
                Bar(
                    ts=dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    adj_close=a,
                    volume=v,
                )
            )
        return BarSeries(ticker=ticker, interval=interval, bars=bars, source="yfinance")
