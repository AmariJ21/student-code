"""
Present-day security metadata (sector, market cap) from Yahoo Finance's
quoteSummary endpoint, fetched directly via `requests` (validated live while
building this module -- the endpoint requires a session cookie + crumb, which
plain `requests` handles fine without needing the `yfinance` package).

IMPORTANT LIMITATION (see FEASIBILITY.md #8): this metadata reflects the
security *today*, not at the time of a historical trade. Sector is a
reasonable static approximation for most companies; market cap is not
(shares outstanding and price both change), so market-cap bucketing of an
old trade using this data is a present-day approximation and is labeled as
such everywhere it's used in analysis/breakdowns.py.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ctbacktest-research/0.1)"}
_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
_QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"

MARKET_CAP_BUCKETS = [
    (0, 300_000_000, "MICRO"),
    (300_000_000, 2_000_000_000, "SMALL"),
    (2_000_000_000, 10_000_000_000, "MID"),
    (10_000_000_000, 200_000_000_000, "LARGE"),
    (200_000_000_000, float("inf"), "MEGA"),
]


def bucket_market_cap(market_cap: float | None) -> str | None:
    if market_cap is None:
        return None
    for lo, hi, label in MARKET_CAP_BUCKETS:
        if lo <= market_cap < hi:
            return label
    return None


class YahooMetadataClient:
    def __init__(self, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)
        self.timeout = timeout
        self._crumb: str | None = None

    def _get_crumb(self) -> str:
        if self._crumb is None:
            self.session.get("https://fc.yahoo.com", timeout=self.timeout)  # sets session cookie; 404 body is expected/ignored
            resp = self.session.get(_CRUMB_URL, timeout=self.timeout)
            resp.raise_for_status()
            self._crumb = resp.text.strip()
        return self._crumb

    def get_profile(self, ticker: str) -> dict | None:
        """Returns {'sector': str|None, 'market_cap': float|None} for a ticker,
        or None if the lookup fails -- never fabricated."""
        try:
            crumb = self._get_crumb()
            resp = self.session.get(
                _QUOTE_SUMMARY_URL.format(ticker=ticker),
                params={"modules": "assetProfile,price", "crumb": crumb},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            logger.warning("Yahoo metadata lookup failed for %s", ticker, exc_info=True)
            return None

        results = (payload.get("quoteSummary") or {}).get("result") or []
        if not results:
            return None
        node = results[0]
        sector = (node.get("assetProfile") or {}).get("sector")
        market_cap = (node.get("price") or {}).get("marketCap")
        if isinstance(market_cap, dict):
            market_cap = market_cap.get("raw")
        return {"sector": sector, "market_cap": market_cap, "market_cap_bucket": bucket_market_cap(market_cap)}
