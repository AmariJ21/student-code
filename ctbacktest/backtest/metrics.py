"""
Performance metrics computed from a completed backtest's trade list and
portfolio equity snapshots (spec section 14).

All functions operate on trades that were NOT excluded (excluded_reason is
None) unless otherwise noted -- excluded trades are counted separately by
`exclusion_summary()` and must always be reported alongside headline numbers,
never silently dropped from the report (see FEASIBILITY.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def trades_to_dataframe(trades: list) -> pd.DataFrame:
    return pd.DataFrame([vars(t) for t in trades])


def exclusion_summary(df: pd.DataFrame) -> dict:
    total = len(df)
    excluded = df[df["excluded_reason"].notna()] if "excluded_reason" in df else df.iloc[0:0]
    counts = excluded["excluded_reason"].value_counts().to_dict() if len(excluded) else {}
    return {
        "total_candidates": total,
        "excluded_total": len(excluded),
        "excluded_pct": len(excluded) / total if total else 0.0,
        "excluded_by_reason": counts,
        "simulated_total": total - len(excluded),
    }


def _simulated(df: pd.DataFrame) -> pd.DataFrame:
    if "excluded_reason" not in df.columns:
        return df
    return df[df["excluded_reason"].isna()].copy()


def return_metrics(df: pd.DataFrame) -> dict:
    sim = _simulated(df)
    if sim.empty:
        return {"total_trades": 0}
    returns = sim["net_return"].dropna()
    if returns.empty:
        return {"total_trades": len(sim)}
    total_return = float((1 + returns).prod() - 1)
    n = len(returns)
    avg_holding_days = sim["holding_period_days"].mean()
    days_span = max((pd.to_datetime(sim["exit_timestamp"]).max() - pd.to_datetime(sim["entry_timestamp"]).min()).days, 1)
    years = days_span / 365.25
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 and (1 + total_return) > 0 else float("nan")
    geometric_return = (1 + returns).prod() ** (1 / n) - 1 if n else float("nan")
    return {
        "total_trades": n,
        "total_return": total_return,
        "cagr": cagr,
        "average_trade_return": float(returns.mean()),
        "median_trade_return": float(returns.median()),
        "geometric_return": float(geometric_return),
        "average_holding_period_days": float(avg_holding_days) if pd.notna(avg_holding_days) else None,
    }


def winloss_metrics(df: pd.DataFrame) -> dict:
    sim = _simulated(df)
    returns = sim["net_return"].dropna()
    if returns.empty:
        return {}
    winners = returns[returns > 0]
    losers = returns[returns <= 0]
    gross_profit = winners.sum()
    gross_loss = -losers.sum()
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else float("nan")
    win_rate = len(winners) / len(returns)
    expectancy = float(returns.mean())
    return {
        "num_trades": len(returns),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": win_rate,
        "average_winner": float(winners.mean()) if len(winners) else None,
        "average_loser": float(losers.mean()) if len(losers) else None,
        "largest_winner": float(winners.max()) if len(winners) else None,
        "largest_loser": float(losers.min()) if len(losers) else None,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
    }


def risk_metrics(equity_snapshots: list[tuple], risk_free_rate: float = 0.0, periods_per_year: int = 252) -> dict:
    """equity_snapshots: list of (ts, equity, cash, open_positions, drawdown)
    as produced by PortfolioState.equity_snapshots -- event-driven, not a
    fixed daily series (see backtest/engine.py: this is an event-driven
    backtester, not a daily mark-to-market one)."""
    if not equity_snapshots:
        return {}
    eq = pd.DataFrame(equity_snapshots, columns=["ts", "equity", "cash", "open_positions", "drawdown"])
    eq = eq.sort_values("ts")
    eq["returns"] = eq["equity"].pct_change()
    rets = eq["returns"].dropna()

    max_drawdown = float(eq["drawdown"].min()) if not eq["drawdown"].empty else 0.0
    avg_drawdown = float(eq.loc[eq["drawdown"] < 0, "drawdown"].mean()) if (eq["drawdown"] < 0).any() else 0.0
    volatility = float(rets.std()) if len(rets) > 1 else float("nan")

    if len(rets) > 1 and rets.std() > 0:
        sharpe = float((rets.mean() - risk_free_rate) / rets.std())
        downside = rets[rets < 0]
        sortino = float((rets.mean() - risk_free_rate) / downside.std()) if len(downside) > 1 and downside.std() > 0 else float("nan")
    else:
        sharpe = float("nan")
        sortino = float("nan")

    total_return = float(eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1) if len(eq) > 1 else 0.0
    calmar = float(total_return / abs(max_drawdown)) if max_drawdown < 0 else float("nan")
    var_95 = float(np.percentile(rets, 5)) if len(rets) > 1 else float("nan")

    return {
        "max_drawdown": max_drawdown,
        "average_drawdown": avg_drawdown,
        "volatility_per_trade_event": volatility,
        "sharpe_ratio_per_trade_event": sharpe,
        "sortino_ratio_per_trade_event": sortino,
        "calmar_ratio": calmar,
        "value_at_risk_95": var_95,
        "note": (
            "Sharpe/Sortino/volatility are computed over per-event (trade open/close) "
            "equity changes, not fixed daily marks, because this is an event-driven "
            "backtester (spec section 12) rather than a daily-rebalanced one. They are "
            "not directly comparable to a daily-frequency Sharpe ratio without further "
            "annualization assumptions, which is why no single number is presented as "
            "'the' annualized Sharpe."
        ),
    }


def strategy_specific_metrics(df: pd.DataFrame, take_profit_levels: list[float] = (0.05, 0.075, 0.10, 0.15, 0.20)) -> dict:
    sim = _simulated(df)
    if sim.empty:
        return {}
    out = {}
    for level in take_profit_levels:
        reached = sim["mfe"] >= level
        pct_label = f"{level*100:g}pct"
        out[f"pct_reaching_{pct_label}"] = float(reached.mean())

    hit_target = sim[sim["exit_reason"] == "TAKE_PROFIT"]
    out["avg_time_to_target_days"] = float(hit_target["holding_period_days"].mean()) if len(hit_target) else None
    out["median_time_to_target_days"] = float(hit_target["holding_period_days"].median()) if len(hit_target) else None
    out["pct_hitting_stop_first"] = float((sim["exit_reason"] == "STOP_LOSS").mean())
    out["pct_timing_out"] = float((sim["exit_reason"] == "TIME_EXIT").mean())
    out["pct_data_ended"] = float((sim["exit_reason"] == "DATA_ENDED").mean())
    out["pct_ambiguous_same_bar"] = float(sim["ambiguous_same_bar"].mean())
    out["average_disclosure_delay_days"] = float(sim["disclosure_delay_days"].mean())
    return out
