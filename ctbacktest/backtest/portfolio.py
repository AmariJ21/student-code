"""
Capital/position-sizing model. The engine can only open a position the
portfolio approves -- this is the single place that enforces "the strategy
cannot magically invest unlimited capital" (spec section 7).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ctbacktest.config import PortfolioConfig, PositionSizingMode


@dataclass
class OpenPosition:
    trade_key: object  # opaque id the engine uses to look this position back up
    ticker: str
    shares: float
    position_value: float
    entry_timestamp: dt.datetime


@dataclass
class PortfolioState:
    config: PortfolioConfig
    cash: float = field(init=False)
    equity_snapshots: list[tuple[dt.datetime, float, float, int, float]] = field(default_factory=list)
    open_positions: dict[object, OpenPosition] = field(default_factory=dict)
    _peak_equity: float = field(init=False)

    def __post_init__(self):
        self.cash = self.config.starting_capital
        self._peak_equity = self.config.starting_capital

    def equity(self, mark_to_market_value: float = 0.0) -> float:
        return self.cash + mark_to_market_value

    def deployed_value(self) -> float:
        return sum(p.position_value for p in self.open_positions.values())

    def can_open_position(self, current_equity: float) -> bool:
        if len(self.open_positions) >= self.config.max_positions:
            return False
        if self.deployed_value() >= current_equity * self.config.max_portfolio_exposure:
            return False
        return True

    def size_position(self, current_equity: float) -> float:
        """Dollar amount to allocate to a new position, before liquidity/exposure capping."""
        mode = self.config.position_sizing_mode
        if mode == PositionSizingMode.FIXED_DOLLAR:
            target = self.config.fixed_dollar_amount or (current_equity * self.config.position_size_pct)
        elif mode == PositionSizingMode.EQUAL_WEIGHT:
            target = current_equity / max(self.config.max_positions, 1)
        else:  # PERCENT_OF_EQUITY
            target = current_equity * self.config.position_size_pct

        room_left = max(current_equity * self.config.max_portfolio_exposure - self.deployed_value(), 0.0)
        target = min(target, room_left, self.cash)
        return max(target, 0.0)

    def open_position(self, trade_key: object, ticker: str, shares: float, position_value: float, ts: dt.datetime) -> None:
        self.cash -= position_value
        self.open_positions[trade_key] = OpenPosition(trade_key, ticker, shares, position_value, ts)

    def close_position(self, trade_key: object, proceeds: float) -> None:
        if trade_key in self.open_positions:
            del self.open_positions[trade_key]
        self.cash += proceeds

    def record_snapshot(self, ts: dt.datetime, mark_to_market_value: float) -> None:
        equity = self.equity(mark_to_market_value)
        self._peak_equity = max(self._peak_equity, equity)
        drawdown = (equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        self.equity_snapshots.append((ts, equity, self.cash, len(self.open_positions), drawdown))
