"""
The same-bar ambiguity problem (spec section 5): when a single bar's [low,
high] range contains both the take-profit and stop-loss thresholds, daily
(or any single) OHLC data cannot tell us which was actually touched first.

Two modes (StrategyConfig.same_bar_mode), both of which *never* assume the
favorable outcome by default:
  - CONSERVATIVE (default): the adverse side (stop-loss) is assumed to have
    triggered first. This is the standard conservative convention -- it may
    understate returns on some individual trades but never overstates the
    strategy's real edge by resolving a coin-flip in its own favor.
  - STRICT_AMBIGUOUS: the trade is flagged `ambiguous_same_bar=True` and
    excluded from headline performance stats (but still counted/reported),
    for a stricter reading that doesn't want the conservative assumption
    baked in either.

When intraday (1m/5m) bars are used instead of daily, the ambiguity mostly
disappears because we can just look at which sub-bar touched which level
first -- this module only matters when operating on daily-resolution bars
(or on any single bar that's coarser than the strategy's move size).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ctbacktest.config import SameBarMode


class ExitTrigger(str, Enum):
    NONE = "NONE"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    AMBIGUOUS = "AMBIGUOUS_SAME_BAR"


@dataclass
class BarOutcome:
    trigger: ExitTrigger
    fill_price: float | None  # target_price or stop_price, whichever triggered (None if NONE/AMBIGUOUS)


def evaluate_bar(
    bar_low: float,
    bar_high: float,
    target_price: float,
    stop_price: float | None,
    same_bar_mode: SameBarMode,
) -> BarOutcome:
    hit_target = bar_high >= target_price
    hit_stop = stop_price is not None and bar_low <= stop_price

    if hit_target and hit_stop:
        if same_bar_mode == SameBarMode.STRICT_AMBIGUOUS:
            return BarOutcome(ExitTrigger.AMBIGUOUS, None)
        # CONSERVATIVE: adverse side wins ties -- never assume the best case.
        return BarOutcome(ExitTrigger.STOP_LOSS, stop_price)

    if hit_target:
        return BarOutcome(ExitTrigger.TAKE_PROFIT, target_price)
    if hit_stop:
        return BarOutcome(ExitTrigger.STOP_LOSS, stop_price)
    return BarOutcome(ExitTrigger.NONE, None)
