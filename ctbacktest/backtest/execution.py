"""
Realistic fill-price model. Never assumes entry_price/exit_price == the raw
market price -- always applies spread + slippage (+ commission separately, as
a cash cost) so the backtest can be re-run across the slippage grid to see
whether any edge survives realistic execution costs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ctbacktest.config import ExecutionConfig


@dataclass
class FillResult:
    fill_price: float
    slippage_cost: float  # in price terms per share, informational
    commission_cost: float  # in dollar terms for the whole trade


def entry_fill_price(raw_price: float, execution: ExecutionConfig) -> float:
    """Buying: spread + slippage always work against us (pay more)."""
    adverse_fraction = execution.bid_ask_spread + execution.entry_slippage
    return raw_price * (1 + adverse_fraction)


def exit_fill_price(raw_price: float, execution: ExecutionConfig) -> float:
    """Selling: spread + slippage always work against us (receive less)."""
    adverse_fraction = execution.bid_ask_spread + execution.exit_slippage
    return raw_price * (1 - adverse_fraction)


def commission_cost(position_value: float, execution: ExecutionConfig) -> float:
    return position_value * execution.commission
