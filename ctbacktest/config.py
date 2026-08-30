"""
Strategy / execution / portfolio configuration.

Every backtest run is fully described by a StrategyConfig + ExecutionConfig +
PortfolioConfig. The triple is hashed (config_hash) so two runs are only ever
compared as "the same experiment" if every parameter matches -- this is the
reproducibility guarantee required by the spec (see IMPLEMENTATION_PLAN.md).

The allowed take-profit / stop-loss / max-hold-day values are deliberately
enumerated (not free-floating) per the spec's requirement that `optimize` runs
a predefined experiment matrix rather than unconstrained parameter mining.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# --- Allowed parameter grids (spec section 3 / 19) -------------------------

ALLOWED_TAKE_PROFITS = [0.05, 0.075, 0.10, 0.15, 0.20]
ALLOWED_STOP_LOSSES = [None, 0.05, 0.075, 0.10, 0.15]
ALLOWED_MAX_HOLD_DAYS = [5, 10, 20, 30, 60]
ALLOWED_SLIPPAGE_BPS = [0.0, 0.0005, 0.0010, 0.0020, 0.0050, 0.0100]
ALLOWED_ENTRY_DELAYS_MINUTES = [0, 5, 15, 30, 60, "next_open"]


class OwnerType(str, Enum):
    SELF = "SELF"
    SPOUSE = "SPOUSE"
    DEPENDENT = "DEPENDENT"
    JOINT = "JOINT"
    UNKNOWN = "UNKNOWN"


class AssetType(str, Enum):
    COMMON_STOCK = "COMMON_STOCK"
    ETF = "ETF"
    OPTION = "OPTION"
    MUTUAL_FUND = "MUTUAL_FUND"
    BOND = "BOND"
    OTHER = "OTHER"


class TransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXCHANGE = "EXCHANGE"
    UNKNOWN = "UNKNOWN"


class DisclosureConfidence(str, Enum):
    EXACT = "EXACT"
    SCRAPER_OBSERVED = "SCRAPER_OBSERVED"
    DATE_ONLY_ASSUMED = "DATE_ONLY_ASSUMED"


class PriceResolution(str, Enum):
    TICK = "tick"
    ONE_MIN = "1m"
    FIVE_MIN = "5m"
    DAILY = "1d"


class SameBarMode(str, Enum):
    CONSERVATIVE = "conservative"  # adverse side wins ties (default, never assumes best case)
    STRICT_AMBIGUOUS = "strict_ambiguous"  # mark AMBIGUOUS_SAME_BAR and exclude from headline stats


class PositionSizingMode(str, Enum):
    PERCENT_OF_EQUITY = "percent_of_equity"
    FIXED_DOLLAR = "fixed_dollar"
    EQUAL_WEIGHT = "equal_weight"


class StrategyConfig(BaseModel):
    take_profit: float = Field(default=0.10)
    stop_loss: Optional[float] = Field(default=None)
    max_hold_days: int = Field(default=30)
    entry_delay_minutes: object = Field(default=0, description="int minutes, or the string 'next_open'")
    same_bar_mode: SameBarMode = Field(default=SameBarMode.CONSERVATIVE)
    include_only_buys: bool = Field(default=True, description="Baseline strategy ignores SELL transactions")

    @field_validator("take_profit")
    @classmethod
    def _tp_allowed(cls, v: float) -> float:
        if v not in ALLOWED_TAKE_PROFITS:
            raise ValueError(f"take_profit must be one of {ALLOWED_TAKE_PROFITS}, got {v}")
        return v

    @field_validator("stop_loss")
    @classmethod
    def _sl_allowed(cls, v: Optional[float]) -> Optional[float]:
        if v not in ALLOWED_STOP_LOSSES:
            raise ValueError(f"stop_loss must be one of {ALLOWED_STOP_LOSSES}, got {v}")
        return v

    @field_validator("max_hold_days")
    @classmethod
    def _hold_allowed(cls, v: int) -> int:
        if v not in ALLOWED_MAX_HOLD_DAYS:
            raise ValueError(f"max_hold_days must be one of {ALLOWED_MAX_HOLD_DAYS}, got {v}")
        return v

    @field_validator("entry_delay_minutes")
    @classmethod
    def _delay_allowed(cls, v: object) -> object:
        if v not in ALLOWED_ENTRY_DELAYS_MINUTES:
            raise ValueError(f"entry_delay_minutes must be one of {ALLOWED_ENTRY_DELAYS_MINUTES}, got {v}")
        return v


class ExecutionConfig(BaseModel):
    entry_slippage: float = Field(default=0.001, description="Fractional adverse slippage applied at entry")
    exit_slippage: float = Field(default=0.001, description="Fractional adverse slippage applied at exit")
    bid_ask_spread: float = Field(default=0.0005, description="Fractional half-spread applied on both sides")
    commission: float = Field(default=0.0, description="Fractional commission per trade side")

    @field_validator("entry_slippage", "exit_slippage")
    @classmethod
    def _slip_allowed(cls, v: float) -> float:
        if round(v, 6) not in [round(x, 6) for x in ALLOWED_SLIPPAGE_BPS]:
            raise ValueError(f"slippage must be one of {ALLOWED_SLIPPAGE_BPS}, got {v}")
        return v


class LiquidityFilterConfig(BaseModel):
    min_avg_daily_volume: Optional[float] = None
    min_price: Optional[float] = None
    min_market_cap: Optional[float] = None
    max_bid_ask_spread: Optional[float] = None


class PortfolioConfig(BaseModel):
    starting_capital: float = Field(default=10_000.0)
    position_size_pct: float = Field(default=0.10)
    position_sizing_mode: PositionSizingMode = Field(default=PositionSizingMode.PERCENT_OF_EQUITY)
    fixed_dollar_amount: Optional[float] = None
    max_positions: int = Field(default=10)
    max_portfolio_exposure: float = Field(default=1.0, description="Max fraction of equity deployed at once")
    liquidity_filters: LiquidityFilterConfig = Field(default_factory=LiquidityFilterConfig)


class BacktestConfig(BaseModel):
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    split_label: str = Field(default="full", description="e.g. train/validation/test/walk_forward_N/full")
    strategy_version: str = Field(default="0.1.0")

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


BASELINE_CONFIG = BacktestConfig(
    strategy=StrategyConfig(take_profit=0.10, stop_loss=None, max_hold_days=30),
)
