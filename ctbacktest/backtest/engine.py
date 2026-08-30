"""
The event-driven backtest engine (spec section 12). This is the ONLY module
allowed to open/close simulated positions -- everything else (metrics,
reporting, dashboard) only reads its output.

Look-ahead-safety invariant (spec section 4), enforced structurally, not just
by convention:
  - Entry timing is derived exclusively from `disclosure_timestamp`/
    `disclosure_date`. `transaction_date` is carried on the candidate only to
    compute `disclosure_delay_days` for reporting -- it is never read by any
    pricing/timing logic below.
  - A ticker's bar series is only ever fetched starting at
    `market_entry_timestamp`; no bar timestamped earlier is ever visible to
    the exit-triggering loop, and MFE/MAE are computed only over bars from
    entry through the actual exit, not the full available series.
"""

from __future__ import annotations

import datetime as dt
import heapq
import itertools
from dataclasses import dataclass
from typing import Optional

from ctbacktest.backtest import execution as exec_model
from ctbacktest.backtest.portfolio import PortfolioState
from ctbacktest.backtest.same_bar import ExitTrigger, evaluate_bar
from ctbacktest.config import BacktestConfig, DisclosureConfidence, PriceResolution
from ctbacktest.market_data.base import BarSeries, MarketDataProvider
from ctbacktest.market_data.corporate_actions import names_plausibly_match
from ctbacktest.market_data.resolution import best_available_series
from ctbacktest.utils.market_calendar import add_trading_days, earliest_tradable_timestamp, next_market_open_at_or_after

# Buffer beyond the longest allowed max_hold_days so the fetched bar series
# has enough room to reach a time-exit even accounting for non-trading days.
_MAX_HOLD_BUFFER_CALENDAR_DAYS = 40


@dataclass
class TradeCandidate:
    transaction_id: int
    politician_id: int
    security_id: int
    ticker: str
    disclosure_date: dt.date
    disclosure_timestamp: Optional[dt.datetime]
    disclosure_confidence: str
    transaction_date: dt.date  # reporting only -- see module docstring
    owner_type: str
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    expected_asset_name: Optional[str] = None  # from the disclosure filing, used only as a ticker-reuse sanity check


@dataclass
class SimulatedTrade:
    transaction_id: int
    politician_id: int
    security_id: int
    ticker: str
    disclosure_delay_days: int
    entry_timestamp: Optional[dt.datetime] = None
    entry_price: Optional[float] = None
    shares: Optional[float] = None
    position_value: Optional[float] = None
    target_price: Optional[float] = None
    stop_price: Optional[float] = None
    exit_timestamp: Optional[dt.datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_return: Optional[float] = None
    slippage_cost: Optional[float] = None
    fees: Optional[float] = None
    net_return: Optional[float] = None
    holding_period_days: Optional[float] = None
    mfe: Optional[float] = None
    mae: Optional[float] = None
    price_resolution: str = PriceResolution.DAILY.value
    disclosure_confidence: str = DisclosureConfidence.DATE_ONLY_ASSUMED.value
    ambiguous_same_bar: bool = False
    excluded_reason: Optional[str] = None


def compute_market_entry_timestamp(candidate: TradeCandidate) -> dt.datetime:
    """See FEASIBILITY.md #5. Never reads transaction_date."""
    if candidate.disclosure_timestamp is not None:
        return earliest_tradable_timestamp(candidate.disclosure_timestamp)
    # Date-only confidence: assume 9:30am ET on/after disclosure_date.
    naive_midnight = dt.datetime.combine(candidate.disclosure_date, dt.time(0, 0))
    eastern_midnight = naive_midnight.replace(tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    return next_market_open_at_or_after(eastern_midnight)


def _apply_entry_delay(base_ts: dt.datetime, entry_delay_minutes) -> dt.datetime:
    if entry_delay_minutes == "next_open":
        return next_market_open_at_or_after(base_ts + dt.timedelta(seconds=1))
    delayed = base_ts + dt.timedelta(minutes=int(entry_delay_minutes))
    return earliest_tradable_timestamp(delayed)


class BacktestEngine:
    def __init__(self, config: BacktestConfig, market_data: MarketDataProvider):
        self.config = config
        self.market_data = market_data
        self.portfolio = PortfolioState(config=config.portfolio)

    def _fetch_series(self, ticker: str, entry_ts: dt.datetime) -> tuple[BarSeries, PriceResolution]:
        end = entry_ts + dt.timedelta(days=max(self.config.strategy.max_hold_days, 60) + _MAX_HOLD_BUFFER_CALENDAR_DAYS)
        return best_available_series(self.market_data, ticker, entry_ts, end)

    def _resolve_entry(self, bars, entry_ts: dt.datetime) -> tuple[Optional[int], Optional[float]]:
        """Returns (index_into_bars, raw_entry_price). `index` is where the
        exit-checking loop should START from.

        Verified live (see comment in yfinance_provider.py's caller and the
        development notes): Yahoo's daily bars are timestamped at that
        session's OPEN, and a fetch starting mid-day still returns that same
        day's bar. So for an intraday (Senate, EXACT) disclosure time with
        only daily resolution available, the day's bar timestamp being before
        entry_ts does NOT mean "no data yet" -- it means "this is today's bar,
        but its open already happened before we could act." In that case the
        only causally-valid same-day price is the close, and exit-checking
        must resume on the NEXT bar, since today's high/low already happened
        before entry.
        """
        for i, bar in enumerate(bars):
            if bar.ts.date() < entry_ts.date():
                continue
            if bar.ts.date() == entry_ts.date():
                if entry_ts <= bar.ts:
                    return i, bar.open
                return i + 1, bar.close
            return i, bar.open  # first bar strictly after the entry day (data gap on the entry day itself)
        return None, None

    def _price_path(self, candidate: TradeCandidate, entry_ts: dt.datetime, trade: SimulatedTrade) -> Optional[dict]:
        """Phase 1: determine the full price outcome for one candidate as if
        it were the only position in the portfolio -- entry/exit prices,
        timing, MFE/MAE, exit reason. This is entirely independent of
        position sizing (per-share prices and return *ratios* don't depend on
        how many dollars/shares are involved), which is what makes phase 2
        (portfolio capacity, chronologically overlapping positions) possible
        without re-deriving the price path. Mutates `trade` in place for the
        fields phase 1 can already determine; returns a dict of the
        additional raw values phase 2 needs, or None if excluded (in which
        case trade.excluded_reason is already set)."""
        strategy = self.config.strategy
        execution = self.config.execution

        series, resolution = self._fetch_series(candidate.ticker, entry_ts)
        if not series.bars:
            trade.excluded_reason = "EXCLUDED_NO_PRICE_DATA"
            return None
        if not names_plausibly_match(candidate.expected_asset_name, series.security_name):
            # Ticker-symbol reuse guard -- see corporate_actions.names_plausibly_match.
            trade.excluded_reason = "EXCLUDED_TICKER_IDENTITY_MISMATCH"
            return None

        start_idx, raw_entry_price = self._resolve_entry(series.bars, entry_ts)
        if start_idx is None or raw_entry_price is None:
            trade.excluded_reason = "EXCLUDED_NO_PRICE_DATA_AFTER_ENTRY"
            return None

        entry_fill = exec_model.entry_fill_price(raw_entry_price, execution)
        target_price = entry_fill * (1 + strategy.take_profit)
        stop_price = entry_fill * (1 - strategy.stop_loss) if strategy.stop_loss else None
        hold_deadline = add_trading_days(entry_ts, strategy.max_hold_days)

        best_high = raw_entry_price
        worst_low = raw_entry_price
        exit_trigger = ExitTrigger.NONE
        exit_raw_price = None
        exit_ts = None
        last_bar_within_deadline = None
        ran_out_of_data = True

        for bar in series.bars[start_idx:]:
            if bar.ts > hold_deadline:
                ran_out_of_data = False
                break
            best_high = max(best_high, bar.high)
            worst_low = min(worst_low, bar.low)
            outcome = evaluate_bar(bar.low, bar.high, target_price, stop_price, strategy.same_bar_mode)
            if outcome.trigger != ExitTrigger.NONE:
                exit_trigger = outcome.trigger
                exit_raw_price = outcome.fill_price if outcome.fill_price is not None else bar.close
                exit_ts = bar.ts
                ran_out_of_data = False
                break
            last_bar_within_deadline = bar
        else:
            ran_out_of_data = True

        if exit_trigger == ExitTrigger.AMBIGUOUS:
            trade.ambiguous_same_bar = True
            trade.exit_reason = "AMBIGUOUS_SAME_BAR"
        elif exit_trigger != ExitTrigger.NONE:
            trade.exit_reason = exit_trigger.value
        elif last_bar_within_deadline is not None:
            exit_raw_price = last_bar_within_deadline.close
            exit_ts = last_bar_within_deadline.ts
            trade.exit_reason = "DATA_ENDED" if ran_out_of_data else "TIME_EXIT"
        else:
            trade.exit_reason = "DATA_ENDED"
            exit_raw_price = raw_entry_price
            exit_ts = entry_ts

        exit_fill = exec_model.exit_fill_price(exit_raw_price, execution)

        trade.entry_timestamp = entry_ts
        trade.entry_price = entry_fill
        trade.exit_timestamp = exit_ts
        trade.exit_price = exit_fill
        trade.target_price = target_price
        trade.stop_price = stop_price
        trade.price_resolution = resolution.value
        trade.gross_return = (exit_raw_price - raw_entry_price) / raw_entry_price
        trade.slippage_cost = (entry_fill - raw_entry_price) + (exit_raw_price - exit_fill)
        trade.holding_period_days = (exit_ts - entry_ts).total_seconds() / 86400.0
        trade.mfe = (best_high - raw_entry_price) / raw_entry_price
        trade.mae = (worst_low - raw_entry_price) / raw_entry_price

        return {"entry_fill": entry_fill, "exit_fill": exit_fill}

    def run(self, candidates: list[TradeCandidate]) -> list[SimulatedTrade]:
        """Phase 2: walk candidates in entry-timestamp order, settling
        (closing) any still-open positions whose exit already occurred
        before considering the next entry -- this is what actually makes
        max_positions/max_portfolio_exposure bind on genuinely overlapping
        holding periods, rather than every trade appearing sequentially
        resolved before the next one is even considered."""
        execution = self.config.execution

        enriched = []
        for c in candidates:
            base_ts = compute_market_entry_timestamp(c)
            entry_ts = _apply_entry_delay(base_ts, self.config.strategy.entry_delay_minutes)
            enriched.append((entry_ts, c))
        enriched.sort(key=lambda pair: pair[0])

        results: list[SimulatedTrade] = []
        # Min-heap of (exit_ts, tie_breaker, portfolio_key, trade, shares, position_dollars, exit_fill).
        # tie_breaker is a unique counter so heapq never needs to compare the
        # (unhashable, uncomparable) SimulatedTrade objects themselves.
        pending_closes: list[tuple[dt.datetime, int, object, "SimulatedTrade", float, float, float]] = []
        tie_breaker = itertools.count()

        def _settle_up_to(cutoff_ts: dt.datetime) -> None:
            while pending_closes and pending_closes[0][0] <= cutoff_ts:
                exit_ts, _, portfolio_key, trade_obj, shares, position_dollars, exit_fill = heapq.heappop(pending_closes)
                proceeds = shares * exit_fill
                commission = exec_model.commission_cost(position_dollars, execution) + exec_model.commission_cost(proceeds, execution)
                proceeds -= commission
                self.portfolio.close_position(portfolio_key, proceeds)
                self.portfolio.record_snapshot(exit_ts, self.portfolio.deployed_value())
                trade_obj.fees = commission
                trade_obj.net_return = (proceeds - position_dollars) / position_dollars

        for entry_ts, candidate in enriched:
            _settle_up_to(entry_ts)

            delay_days = (candidate.disclosure_date - candidate.transaction_date).days
            trade = SimulatedTrade(
                transaction_id=candidate.transaction_id,
                politician_id=candidate.politician_id,
                security_id=candidate.security_id,
                ticker=candidate.ticker,
                disclosure_delay_days=delay_days,
                disclosure_confidence=candidate.disclosure_confidence,
            )

            priced = self._price_path(candidate, entry_ts, trade)
            if priced is None:
                results.append(trade)
                continue

            current_equity = self.portfolio.equity(self.portfolio.deployed_value())
            if not self.portfolio.can_open_position(current_equity):
                trade.excluded_reason = "EXCLUDED_PORTFOLIO_CAPACITY"
                results.append(trade)
                continue

            position_dollars = self.portfolio.size_position(current_equity)
            if position_dollars <= 0:
                trade.excluded_reason = "EXCLUDED_INSUFFICIENT_CAPITAL"
                results.append(trade)
                continue

            shares = position_dollars / priced["entry_fill"]
            trade.shares = shares
            trade.position_value = position_dollars

            portfolio_key = object()
            self.portfolio.open_position(portfolio_key, candidate.ticker, shares, position_dollars, entry_ts)
            self.portfolio.record_snapshot(entry_ts, self.portfolio.deployed_value())

            heapq.heappush(
                pending_closes,
                (trade.exit_timestamp, next(tie_breaker), portfolio_key, trade, shares, position_dollars, priced["exit_fill"]),
            )

            results.append(trade)

        _settle_up_to(dt.datetime.max.replace(tzinfo=dt.timezone.utc))
        return results
