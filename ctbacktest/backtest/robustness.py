"""
Robustness sweeps (spec section 20): re-run the same candidate set across the
slippage grid, the entry-delay grid, and the TP/SL/hold-day grid, and compare
-- rather than reporting only the single base-case configuration.
"""

from __future__ import annotations

import copy

from ctbacktest.backtest.engine import BacktestEngine, TradeCandidate
from ctbacktest.backtest.metrics import return_metrics, trades_to_dataframe, winloss_metrics
from ctbacktest.config import (
    ALLOWED_ENTRY_DELAYS_MINUTES,
    ALLOWED_MAX_HOLD_DAYS,
    ALLOWED_SLIPPAGE_BPS,
    ALLOWED_STOP_LOSSES,
    ALLOWED_TAKE_PROFITS,
    BacktestConfig,
)
from ctbacktest.market_data.base import MarketDataProvider


def _run_variant(candidates: list[TradeCandidate], config: BacktestConfig, market_data: MarketDataProvider) -> dict:
    engine = BacktestEngine(config, market_data)
    trades = engine.run(candidates)
    df = trades_to_dataframe(trades)
    return {**return_metrics(df), **winloss_metrics(df)}


def slippage_sweep(candidates: list[TradeCandidate], base_config: BacktestConfig, market_data: MarketDataProvider) -> list[dict]:
    results = []
    for slip in ALLOWED_SLIPPAGE_BPS:
        cfg = copy.deepcopy(base_config)
        cfg.execution.entry_slippage = slip
        cfg.execution.exit_slippage = slip
        results.append({"entry_exit_slippage": slip, **_run_variant(candidates, cfg, market_data)})
    return results


def entry_delay_sweep(candidates: list[TradeCandidate], base_config: BacktestConfig, market_data: MarketDataProvider) -> list[dict]:
    results = []
    for delay in ALLOWED_ENTRY_DELAYS_MINUTES:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.entry_delay_minutes = delay
        results.append({"entry_delay_minutes": delay, **_run_variant(candidates, cfg, market_data)})
    return results


def take_profit_sweep(candidates: list[TradeCandidate], base_config: BacktestConfig, market_data: MarketDataProvider) -> list[dict]:
    results = []
    for tp in ALLOWED_TAKE_PROFITS:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.take_profit = tp
        results.append({"take_profit": tp, **_run_variant(candidates, cfg, market_data)})
    return results


def stop_loss_sweep(candidates: list[TradeCandidate], base_config: BacktestConfig, market_data: MarketDataProvider) -> list[dict]:
    results = []
    for sl in ALLOWED_STOP_LOSSES:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.stop_loss = sl
        results.append({"stop_loss": sl, **_run_variant(candidates, cfg, market_data)})
    return results


def max_hold_days_sweep(candidates: list[TradeCandidate], base_config: BacktestConfig, market_data: MarketDataProvider) -> list[dict]:
    results = []
    for days in ALLOWED_MAX_HOLD_DAYS:
        cfg = copy.deepcopy(base_config)
        cfg.strategy.max_hold_days = days
        results.append({"max_hold_days": days, **_run_variant(candidates, cfg, market_data)})
    return results


def full_robustness_report(candidates: list[TradeCandidate], base_config: BacktestConfig, market_data: MarketDataProvider) -> dict:
    return {
        "slippage_sweep": slippage_sweep(candidates, base_config, market_data),
        "entry_delay_sweep": entry_delay_sweep(candidates, base_config, market_data),
        "take_profit_sweep": take_profit_sweep(candidates, base_config, market_data),
        "stop_loss_sweep": stop_loss_sweep(candidates, base_config, market_data),
        "max_hold_days_sweep": max_hold_days_sweep(candidates, base_config, market_data),
    }
