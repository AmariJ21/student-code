"""
Spec section 16 -- "the most important analysis": is any observed edge broad
(Congress as a whole) or is it concentrated in a small number of politicians,
sectors, transaction sizes, or disclosure-delay buckets, or is it just
market-wide momentum unrelated to Congress specifically? This module answers
that with concrete, checkable numbers rather than a single headline return.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def concentration_by_group(group_summary: pd.DataFrame, total_return_col: str = "average_return") -> dict:
    """Given a by_politician()/by_sector()/etc. summary table, compute how
    much of total P&L comes from the top few groups vs. the long tail. A
    profitable strategy where the top 3 politicians/sectors account for
    >80% of gross profit is evidence of (B)/(C)/(D)/(E) in spec section 16,
    not (A) broad-based congressional signal."""
    if group_summary.empty:
        return {"n_groups": 0}
    contrib = group_summary["trade_count"] * group_summary[total_return_col]
    positive_contrib = contrib[contrib > 0]
    total_positive = positive_contrib.sum()
    ranked = positive_contrib.sort_values(ascending=False)
    top_n_share = {}
    for n in (1, 3, 5, 10):
        top_n_share[f"top_{n}_share_of_gross_profit"] = (
            float(ranked.head(n).sum() / total_positive) if total_positive > 0 else float("nan")
        )
    return {
        "n_groups": len(group_summary),
        "n_groups_contributing_positively": len(positive_contrib),
        **top_n_share,
    }


def herfindahl_index(group_summary: pd.DataFrame, weight_col: str = "trade_count") -> float:
    """0 = perfectly diffuse (every group the same size), 1 = fully
    concentrated in one group. A quick single-number concentration signal to
    put next to the top-N shares above."""
    if group_summary.empty or group_summary[weight_col].sum() == 0:
        return float("nan")
    shares = group_summary[weight_col] / group_summary[weight_col].sum()
    return float((shares**2).sum())


def market_wide_momentum_check(strategy_mean_return: float, spy_benchmark_returns: list[float]) -> dict:
    """Spec 16.F: is the return just generic market drift over the same
    holding periods, unrelated to Congress specifically? Compares the
    strategy's mean return to the mean of the SPY buy-hold-same-window
    benchmark (spec section 17, benchmark 1)."""
    if not spy_benchmark_returns:
        return {"spy_mean_return": None, "excess_over_spy": None}
    spy_mean = float(np.mean(spy_benchmark_returns))
    return {
        "spy_mean_return": spy_mean,
        "excess_over_spy": strategy_mean_return - spy_mean,
        "interpretation": (
            "If excess_over_spy is small/negative, the strategy's return is largely "
            "explained by holding equities over the same windows the market happened "
            "to be rising, not by the congressional signal specifically (spec 16.F)."
        ),
    }
