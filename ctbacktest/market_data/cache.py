"""
Local Parquet-backed cache in front of any MarketDataProvider, so a multi-year,
many-ticker backtest doesn't re-hit the network (and Yahoo's rate limits) on
every run. Cache key is (ticker, interval, date-of-start, date-of-end); a
request is served from cache only if the cached range fully covers the
requested range.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd

from ctbacktest.market_data.base import Bar, BarSeries, MarketDataProvider

DEFAULT_CACHE_DIR = Path.home() / ".ctbacktest_cache" / "price_bars"


class CachingProvider(MarketDataProvider):
    def __init__(self, inner: MarketDataProvider, cache_dir: Path | str = DEFAULT_CACHE_DIR):
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, ticker: str, interval: str) -> Path:
        key = hashlib.sha256(f"{ticker}|{interval}".encode()).hexdigest()[:16]
        return self.cache_dir / f"{ticker.upper()}_{interval}_{key}.parquet"

    def max_interval_lookback(self, interval: str, as_of: dt.datetime) -> dt.datetime | None:
        return self.inner.max_interval_lookback(interval, as_of)

    def get_bars(self, ticker: str, start: dt.datetime, end: dt.datetime, interval: str) -> BarSeries:
        path = self._cache_path(ticker, interval)
        if path.exists():
            df = pd.read_parquet(path)
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            covered = (not df.empty) and df["ts"].min() <= start and df["ts"].max() >= end
            if covered:
                mask = (df["ts"] >= start) & (df["ts"] <= end)
                sub = df.loc[mask]
                bars = [
                    Bar(
                        ts=row.ts.to_pydatetime(),
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        adj_close=row.adj_close if pd.notna(row.adj_close) else None,
                        volume=row.volume if pd.notna(row.volume) else None,
                    )
                    for row in sub.itertuples()
                ]
                return BarSeries(ticker=ticker, interval=interval, bars=bars, source=f"{self.inner.__class__.__name__}(cached)")

        series = self.inner.get_bars(ticker, start, end, interval)
        if series.bars:
            new_df = pd.DataFrame(
                [
                    {
                        "ts": b.ts,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "adj_close": b.adj_close,
                        "volume": b.volume,
                    }
                    for b in series.bars
                ]
            )
            if path.exists():
                existing = pd.read_parquet(path)
                existing["ts"] = pd.to_datetime(existing["ts"], utc=True)
                combined = pd.concat([existing, new_df]).drop_duplicates(subset="ts").sort_values("ts")
            else:
                combined = new_df.sort_values("ts")
            combined.to_parquet(path, index=False)
        return series
