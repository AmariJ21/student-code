"""
Breakdowns of trade-level results by politician / chamber / owner / size /
sector / market-cap / disclosure-delay bucket (spec section 15). Every
function takes the same trades dataframe (from backtest/metrics.py) joined
with the politician/security metadata needed for that particular cut, and
returns a per-group summary table -- never a single aggregate number, so a
concentrated result (spec section 16) is visible rather than averaged away.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRANSACTION_SIZE_BUCKETS = [
    (1_000, 15_000, "$1k-$15k"),
    (15_000, 50_000, "$15k-$50k"),
    (50_000, 100_000, "$50k-$100k"),
    (100_000, 250_000, "$100k-$250k"),
    (250_000, 1_000_000, "$250k-$1M"),
    (1_000_000, float("inf"), "$1M+"),
]

DISCLOSURE_DELAY_BUCKETS = [
    (0, 3, "0-3 days"),
    (4, 7, "4-7 days"),
    (8, 14, "8-14 days"),
    (15, 30, "15-30 days"),
    (31, 45, "31-45 days"),
    (46, float("inf"), "45+ days"),
]


def _bucket(value: float, buckets: list[tuple[float, float, str]]) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    for lo, hi, label in buckets:
        if lo <= value < hi:
            return label
    return None


def _group_summary(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    sim = df[df["excluded_reason"].isna()].copy()
    sim = sim.dropna(subset=["net_return", group_col])
    if sim.empty:
        return pd.DataFrame()

    def _agg(g: pd.DataFrame) -> pd.Series:
        returns = g["net_return"]
        wins = returns[returns > 0]
        losses = returns[returns <= 0]
        gross_profit = wins.sum()
        gross_loss = -losses.sum()
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.nan
        sharpe = returns.mean() / returns.std() if returns.std() > 0 and len(returns) > 1 else np.nan
        return pd.Series(
            {
                "trade_count": len(g),
                "win_rate": (returns > 0).mean(),
                "average_return": returns.mean(),
                "median_return": returns.median(),
                "profit_factor": profit_factor,
                "sharpe_like": sharpe,
            }
        )

    return sim.groupby(group_col).apply(_agg, include_groups=False).reset_index().sort_values("trade_count", ascending=False)


def by_politician(df: pd.DataFrame, politicians: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(politicians[["politician_id", "full_name", "chamber"]], on="politician_id", how="left")
    return _group_summary(merged, "full_name")


def by_chamber(df: pd.DataFrame, politicians: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(politicians[["politician_id", "chamber"]], on="politician_id", how="left")
    return _group_summary(merged, "chamber")


def by_owner(df: pd.DataFrame, owner_types: pd.Series) -> pd.DataFrame:
    merged = df.copy()
    merged["owner_type"] = owner_types
    return _group_summary(merged, "owner_type")


def by_transaction_size(df: pd.DataFrame, amounts: pd.Series) -> pd.DataFrame:
    merged = df.copy()
    merged["size_bucket"] = amounts.apply(lambda v: _bucket(v, TRANSACTION_SIZE_BUCKETS))
    return _group_summary(merged, "size_bucket")


def by_sector(df: pd.DataFrame, securities: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(securities[["security_id", "sector"]], on="security_id", how="left")
    result = _group_summary(merged, "sector")
    if not result.empty:
        result.attrs["caveat"] = "Sector is present-day metadata, not sector-at-time-of-trade -- see FEASIBILITY.md #8."
    return result


def by_market_cap_bucket(df: pd.DataFrame, securities: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(securities[["security_id", "market_cap_bucket_asof"]], on="security_id", how="left")
    result = _group_summary(merged, "market_cap_bucket_asof")
    if not result.empty:
        result.attrs["caveat"] = "Market cap bucket is a present-day approximation, not market-cap-at-time-of-trade -- see FEASIBILITY.md #8."
    return result


def by_disclosure_delay(df: pd.DataFrame) -> pd.DataFrame:
    merged = df.copy()
    merged["delay_bucket"] = merged["disclosure_delay_days"].apply(lambda v: _bucket(v, DISCLOSURE_DELAY_BUCKETS))
    return _group_summary(merged, "delay_bucket")


def by_instrument_kind(df: pd.DataFrame) -> pd.DataFrame:
    """STOCK vs OPTION -- the researched motif's central question: does the
    edge (if any) actually live in the option leg, as the individual
    standout performers suggest, or does it show up in plain stock too?"""
    return _group_summary(df, "instrument_kind")
