"""
The event-driven backtest engine (spec section 12, extended for the
options/long-hold/leaderboard motif -- see FEASIBILITY.md's "Restructuring"
section). This is the ONLY module allowed to open/close simulated positions
-- everything else (metrics, reporting, dashboard) only reads its output.

Look-ahead-safety invariant (spec section 4), enforced structurally, not just
by convention:
  - Entry timing is derived exclusively from `disclosure_timestamp`/
    `disclosure_date`. `transaction_date` is carried on the candidate only to
    compute `disclosure_delay_days` for reporting -- it is never read by any
    pricing/timing logic below.
  - A ticker's bar series is only ever fetched starting at
    `market_entry_timestamp`; no bar timestamped earlier is ever visible to
    the exit-triggering loop, and MFE/MAE are computed only over bars from
    entry through the actual exit, not the full available series. (The one
    deliberate exception is the *pre-entry* window used to estimate realized
    volatility for option pricing -- a legitimate use of history available
    before the decision point, not a look-ahead: see _price_path_option.)
  - Politician-leaderboard eligibility is computed from trades that have
    ALREADY CLOSED as of the candidate's own entry time -- see
    backtest/leaderboard.py.
"""

from __future__ import annotations

import datetime as dt
import heapq
import itertools
from dataclasses import dataclass
from typing import Optional

from ctbacktest.backtest import execution as exec_model
from ctbacktest.backtest import options as options_model
from ctbacktest.backtest.leaderboard import PoliticianLeaderboard
from ctbacktest.backtest.portfolio import PortfolioState
from ctbacktest.backtest.same_bar import ExitTrigger, evaluate_bar
from ctbacktest.config import BacktestConfig, DisclosureConfidence, HoldingMode, PoliticianSelectionMode, PriceResolution
from ctbacktest.market_data.base import Bar, BarSeries, MarketDataProvider
from ctbacktest.market_data.corporate_actions import names_plausibly_match
from ctbacktest.market_data.resolution import best_available_series
from ctbacktest.utils.market_calendar import add_trading_days, earliest_tradable_timestamp, next_market_open_at_or_after

# Buffer beyond the longest allowed max_hold_days so the fetched bar series
# has enough room to reach a time-exit even accounting for non-trading days.
_MAX_HOLD_BUFFER_CALENDAR_DAYS = 40
# LONG_HOLD mode has no max_hold_days ceiling, so it needs a much longer
# fetch window -- bounded by this horizon rather than "unlimited", so a
# single candidate can't force an unbounded-size request.
_LONG_HOLD_FETCH_HORIZON_DAYS = 365 * 8


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
    instrument_kind: str = "STOCK"  # "STOCK" or "OPTION"
    option_type: Optional[str] = None  # "CALL" / "PUT"
    strike_price: Optional[float] = None
    expiration_date: Optional[dt.date] = None


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

    instrument_kind: str = "STOCK"
    option_type: Optional[str] = None
    strike_price: Optional[float] = None
    expiration_date: Optional[dt.date] = None
    modeled_volatility: Optional[float] = None
    underlying_entry_price: Optional[float] = None
    underlying_exit_price: Optional[float] = None
    exercised_and_held_underlying: bool = False


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


def _resolve_entry(bars: list[Bar], entry_ts: dt.datetime) -> tuple[Optional[int], Optional[float]]:
    """Returns (index_into_bars, raw_entry_price). `index` is where the
    exit-checking loop should START from.

    Verified live: Yahoo's daily bars are timestamped at that session's OPEN,
    and a fetch starting mid-day still returns that same day's bar. So for an
    intraday (Senate, EXACT) disclosure time with only daily resolution
    available, the day's bar timestamp being before entry_ts does NOT mean
    "no data yet" -- it means "this is today's bar, but its open already
    happened before we could act." In that case the only causally-valid
    same-day price is the close, and exit-checking must resume on the NEXT
    bar, since today's high/low already happened before entry.
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


class BacktestEngine:
    def __init__(self, config: BacktestConfig, market_data: MarketDataProvider):
        self.config = config
        self.market_data = market_data
        self.portfolio = PortfolioState(config=config.portfolio)
        self.leaderboard = PoliticianLeaderboard(
            lookback_days=config.strategy.leaderboard_lookback_days,
            min_track_record_trades=config.strategy.leaderboard_min_track_record_trades,
            top_k=config.strategy.leaderboard_top_k,
        )

    def _fetch_series(self, ticker: str, entry_ts: dt.datetime) -> tuple[BarSeries, PriceResolution]:
        strategy = self.config.strategy
        if strategy.holding_mode == HoldingMode.LONG_HOLD:
            horizon_days = _LONG_HOLD_FETCH_HORIZON_DAYS
        else:
            horizon_days = max(strategy.max_hold_days, 60) + _MAX_HOLD_BUFFER_CALENDAR_DAYS
        end = entry_ts + dt.timedelta(days=horizon_days)
        return best_available_series(self.market_data, ticker, entry_ts, end)

    def _fetch_pre_entry_history(self, ticker: str, entry_ts: dt.datetime, lookback_days: int) -> BarSeries:
        """History strictly before `entry_ts`, for volatility estimation only
        -- see the options module docstring. This is legitimate: it's
        information that existed before the trade decision, not a look-ahead."""
        start = entry_ts - dt.timedelta(days=lookback_days * 2 + 10)
        series, _ = best_available_series(self.market_data, ticker, start, entry_ts)
        return series

    # ---------------------------------------------------------------- Phase 1

    def _price_path(self, candidate: TradeCandidate, entry_ts: dt.datetime, trade: SimulatedTrade) -> Optional[dict]:
        """Determine the full price outcome for one candidate as if it were
        the only position in the portfolio. Returns a dict with entry_fill,
        exit_fill, and contract_multiplier (100 for options, 1 for stock) --
        the only pieces phase 2 (portfolio sizing/settlement) needs, so it
        never has to know about STOCK vs OPTION specifics itself. Returns
        None if excluded (trade.excluded_reason is set in that case)."""
        strategy = self.config.strategy

        series, resolution = self._fetch_series(candidate.ticker, entry_ts)
        if not series.bars:
            trade.excluded_reason = "EXCLUDED_NO_PRICE_DATA"
            return None
        if not names_plausibly_match(candidate.expected_asset_name, series.security_name):
            trade.excluded_reason = "EXCLUDED_TICKER_IDENTITY_MISMATCH"
            return None

        start_idx, raw_entry_price = _resolve_entry(series.bars, entry_ts)
        if start_idx is None or raw_entry_price is None:
            trade.excluded_reason = "EXCLUDED_NO_PRICE_DATA_AFTER_ENTRY"
            return None

        trade.price_resolution = resolution.value
        trade.instrument_kind = candidate.instrument_kind

        if candidate.instrument_kind == "OPTION":
            return self._price_path_option(candidate, entry_ts, trade, series.bars, start_idx, raw_entry_price)
        if strategy.holding_mode == HoldingMode.LONG_HOLD:
            return self._price_path_stock_long_hold(entry_ts, trade, series.bars, start_idx, raw_entry_price)
        return self._price_path_stock_short_term(entry_ts, trade, series.bars, start_idx, raw_entry_price)

    def _price_path_stock_short_term(
        self, entry_ts: dt.datetime, trade: SimulatedTrade, bars: list[Bar], start_idx: int, raw_entry_price: float
    ) -> dict:
        strategy = self.config.strategy
        execution = self.config.execution

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

        for bar in bars[start_idx:]:
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
        trade.gross_return = (exit_raw_price - raw_entry_price) / raw_entry_price
        trade.slippage_cost = (entry_fill - raw_entry_price) + (exit_raw_price - exit_fill)
        trade.holding_period_days = (exit_ts - entry_ts).total_seconds() / 86400.0
        trade.mfe = (best_high - raw_entry_price) / raw_entry_price
        trade.mae = (worst_low - raw_entry_price) / raw_entry_price

        return {"entry_fill": entry_fill, "exit_fill": exit_fill, "contract_multiplier": 1.0}

    def _price_path_stock_long_hold(
        self, entry_ts: dt.datetime, trade: SimulatedTrade, bars: list[Bar], start_idx: int, raw_entry_price: float
    ) -> dict:
        """LONG_HOLD mode (the researched motif): no fixed target or max
        hold -- ride the position, protected only by a trailing stop from its
        post-entry peak, until the trailing stop fires or the data runs out."""
        strategy = self.config.strategy
        execution = self.config.execution
        trailing_pct = strategy.long_hold_trailing_stop_pct

        entry_fill = exec_model.entry_fill_price(raw_entry_price, execution)
        peak = raw_entry_price
        worst_low = raw_entry_price
        exit_raw_price = None
        exit_ts = None
        exit_reason = "DATA_ENDED"

        for bar in bars[start_idx:]:
            peak = max(peak, bar.high)
            worst_low = min(worst_low, bar.low)
            if trailing_pct is not None:
                stop_level = peak * (1 - trailing_pct)
                if bar.low <= stop_level:
                    exit_raw_price = stop_level
                    exit_ts = bar.ts
                    exit_reason = "TRAILING_STOP"
                    break
            exit_raw_price = bar.close
            exit_ts = bar.ts

        if exit_ts is None:
            exit_raw_price = raw_entry_price
            exit_ts = entry_ts

        exit_fill = exec_model.exit_fill_price(exit_raw_price, execution)

        trade.entry_timestamp = entry_ts
        trade.entry_price = entry_fill
        trade.exit_timestamp = exit_ts
        trade.exit_price = exit_fill
        trade.exit_reason = exit_reason
        trade.gross_return = (exit_raw_price - raw_entry_price) / raw_entry_price
        trade.slippage_cost = (entry_fill - raw_entry_price) + (exit_raw_price - exit_fill)
        trade.holding_period_days = (exit_ts - entry_ts).total_seconds() / 86400.0
        trade.mfe = (peak - raw_entry_price) / raw_entry_price
        trade.mae = (worst_low - raw_entry_price) / raw_entry_price

        return {"entry_fill": entry_fill, "exit_fill": exit_fill, "contract_multiplier": 1.0}

    def _price_path_option(
        self,
        candidate: TradeCandidate,
        entry_ts: dt.datetime,
        trade: SimulatedTrade,
        bars: list[Bar],
        start_idx: int,
        underlying_entry_price: float,
    ) -> Optional[dict]:
        """Prices and walks an option position via Black-Scholes -- see
        backtest/options.py for the pricing model and its documented
        approximations (European exercise, realized-vol-as-IV proxy, coarse
        rate/dividend constants). No intrabar high/low modeling is attempted
        for the option's own value (unlike stock): each bar's CLOSE is used
        to reprice once per day, so there is no same-bar ambiguity to
        resolve, but this is also coarser than the stock path's intrabar
        checking -- a real intraday move through a target could be missed
        until the next close. This is stated, not hidden."""
        strategy = self.config.strategy
        execution = self.config.execution

        option_type = options_model.OptionType(candidate.option_type)
        expiration = candidate.expiration_date
        t0 = options_model.year_fraction(entry_ts.date(), expiration)
        if t0 <= 0:
            trade.excluded_reason = "EXCLUDED_OPTION_EXPIRED_BEFORE_ENTRY"
            return None

        history = self._fetch_pre_entry_history(candidate.ticker, entry_ts, strategy.option_iv_lookback_days)
        volatility = options_model.trailing_realized_volatility(history.bars, entry_ts, strategy.option_iv_lookback_days)

        def _price_at(spot: float, as_of_date: dt.date) -> float:
            t = options_model.year_fraction(as_of_date, expiration)
            return options_model.black_scholes_price(
                spot, candidate.strike_price, t, volatility, strategy.option_risk_free_rate,
                strategy.option_dividend_yield_default, option_type,
            )

        raw_entry_option_price = _price_at(underlying_entry_price, entry_ts.date())
        if raw_entry_option_price <= 0.005:
            trade.excluded_reason = "EXCLUDED_OPTION_NEGLIGIBLE_ENTRY_VALUE"
            return None
        entry_fill = exec_model.entry_fill_price(raw_entry_option_price, execution)

        long_hold = strategy.holding_mode == HoldingMode.LONG_HOLD
        target_price = None if long_hold else entry_fill * (1 + strategy.take_profit)
        stop_price = None if long_hold or not strategy.stop_loss else entry_fill * (1 - strategy.stop_loss)
        hold_deadline = None if long_hold else add_trading_days(entry_ts, strategy.max_hold_days)

        peak_value = raw_entry_option_price
        worst_value = raw_entry_option_price
        exit_raw_price = raw_entry_option_price
        exit_ts = entry_ts
        exit_reason = "DATA_ENDED"
        underlying_exit_price = underlying_entry_price
        exercised_and_held = False

        for bar in bars[start_idx:]:
            bar_date = bar.ts.date()
            reached_expiration = bar_date >= expiration
            option_value = _price_at(bar.close, min(bar_date, expiration))
            peak_value = max(peak_value, option_value)
            worst_value = min(worst_value, option_value)
            exit_raw_price = option_value
            exit_ts = bar.ts
            underlying_exit_price = bar.close

            if not long_hold and hold_deadline is not None and bar.ts > hold_deadline:
                exit_reason = "TIME_EXIT"
                break
            if not long_hold and target_price is not None and option_value >= target_price:
                exit_reason = "TAKE_PROFIT"
                break
            if not long_hold and stop_price is not None and option_value <= stop_price:
                exit_reason = "STOP_LOSS"
                break
            if long_hold and strategy.long_hold_trailing_stop_pct is not None:
                if option_value <= peak_value * (1 - strategy.long_hold_trailing_stop_pct):
                    exit_reason = "TRAILING_STOP"
                    break
            if reached_expiration:
                intrinsic = max(bar.close - candidate.strike_price, 0.0) if option_type == options_model.OptionType.CALL else max(candidate.strike_price - bar.close, 0.0)
                exit_raw_price = intrinsic
                exit_reason = "OPTION_EXPIRED_WORTHLESS" if intrinsic <= 0 else "OPTION_EXPIRED_ITM"
                if long_hold and intrinsic > 0 and strategy.long_hold_exercise_and_hold_underlying:
                    exercised_and_held = True
                    exit_reason = "EXERCISED_ROLLED_TO_UNDERLYING"
                    rollover = self._continue_as_underlying_long_hold(intrinsic, bar.close, bars, bar)
                    if rollover is not None:
                        exit_raw_price, exit_ts, underlying_exit_price, exit_reason = rollover
                break
        else:
            exit_reason = "DATA_ENDED"

        exit_fill = exec_model.exit_fill_price(max(exit_raw_price, 0.0), execution)

        trade.entry_timestamp = entry_ts
        trade.entry_price = entry_fill
        trade.exit_timestamp = exit_ts
        trade.exit_price = exit_fill
        trade.exit_reason = exit_reason
        trade.target_price = target_price
        trade.stop_price = stop_price
        trade.gross_return = (exit_raw_price - raw_entry_option_price) / raw_entry_option_price
        trade.slippage_cost = (entry_fill - raw_entry_option_price) + (max(exit_raw_price, 0.0) - exit_fill)
        trade.holding_period_days = (exit_ts - entry_ts).total_seconds() / 86400.0
        # NOTE: mfe/mae only cover the pre-expiration option-value phase; if
        # the position rolled into an underlying stock leg (exercised_and_held
        # True), the stock-phase excursion isn't folded back in here -- a
        # documented scope boundary, not a silent gap (gross_return/net_return
        # DO correctly reflect the full option-then-stock lifetime).
        trade.mfe = (peak_value - raw_entry_option_price) / raw_entry_option_price
        trade.mae = (worst_value - raw_entry_option_price) / raw_entry_option_price
        trade.instrument_kind = "OPTION"
        trade.option_type = candidate.option_type
        trade.strike_price = candidate.strike_price
        trade.expiration_date = candidate.expiration_date
        trade.modeled_volatility = volatility
        trade.underlying_entry_price = underlying_entry_price
        trade.underlying_exit_price = underlying_exit_price
        trade.exercised_and_held_underlying = exercised_and_held

        return {"entry_fill": entry_fill, "exit_fill": exit_fill, "contract_multiplier": 100.0}

    def _continue_as_underlying_long_hold(
        self, intrinsic_value_at_expiration: float, underlying_price_at_expiration: float, all_bars: list[Bar], from_bar: Bar
    ) -> Optional[tuple[float, dt.datetime, float, str]]:
        """Value-equivalent rollover at option expiration (see StrategyConfig.
        long_hold_exercise_and_hold_underlying docstring for why this models
        the exercise as realizing value into an equivalent stock position
        rather than simulating the actual strike-price cash outlay): the
        realized intrinsic value is scaled by the underlying's subsequent
        percentage move, so a later underlying price of e.g. 1.20x the
        rollover price yields 1.20x the intrinsic value as the final
        "option-equivalent" exit value -- keeping the caller's single
        gross_return = (exit - entry_premium) / entry_premium calculation
        correct across the whole option-then-stock lifetime, without
        conflating the underlying's price level with the option's premium.
        Returns (exit_value_in_option_premium_terms, exit_ts,
        underlying_exit_price, exit_reason)."""
        strategy = self.config.strategy
        trailing_pct = strategy.long_hold_trailing_stop_pct
        remaining = [b for b in all_bars if b.ts > from_bar.ts]
        if not remaining or underlying_price_at_expiration <= 0:
            return None

        peak_underlying = underlying_price_at_expiration
        underlying_exit = underlying_price_at_expiration
        exit_ts = from_bar.ts
        for bar in remaining:
            peak_underlying = max(peak_underlying, bar.high)
            underlying_exit = bar.close
            exit_ts = bar.ts
            if trailing_pct is not None and bar.low <= peak_underlying * (1 - trailing_pct):
                underlying_exit = peak_underlying * (1 - trailing_pct)
                scaled_value = intrinsic_value_at_expiration * (underlying_exit / underlying_price_at_expiration)
                return (scaled_value, exit_ts, underlying_exit, "EXERCISED_THEN_TRAILING_STOP")
        scaled_value = intrinsic_value_at_expiration * (underlying_exit / underlying_price_at_expiration)
        return (scaled_value, exit_ts, underlying_exit, "EXERCISED_THEN_DATA_ENDED")

    # ---------------------------------------------------------------- Phase 2

    def run(self, candidates: list[TradeCandidate]) -> list[SimulatedTrade]:
        """Walk candidates in entry-timestamp order, settling (closing) any
        still-open positions -- and updating the politician leaderboard with
        their realized outcome -- before considering the next entry. This is
        what makes max_positions/max_portfolio_exposure and the leaderboard
        both bind on genuinely overlapping, causally-ordered holding periods."""
        execution = self.config.execution
        strategy = self.config.strategy

        enriched = []
        for c in candidates:
            base_ts = compute_market_entry_timestamp(c)
            entry_ts = _apply_entry_delay(base_ts, strategy.entry_delay_minutes)
            enriched.append((entry_ts, c))
        enriched.sort(key=lambda pair: pair[0])

        results: list[SimulatedTrade] = []
        # Min-heap of (exit_ts, tie_breaker, portfolio_key, trade, quantity, position_dollars, exit_fill, contract_multiplier).
        pending_closes: list[tuple] = []
        tie_breaker = itertools.count()

        def _settle_up_to(cutoff_ts: dt.datetime) -> None:
            while pending_closes and pending_closes[0][0] <= cutoff_ts:
                exit_ts, _, portfolio_key, trade_obj, quantity, position_dollars, exit_fill, multiplier = heapq.heappop(pending_closes)
                proceeds = quantity * exit_fill * multiplier
                commission = exec_model.commission_cost(position_dollars, execution) + exec_model.commission_cost(proceeds, execution)
                proceeds -= commission
                self.portfolio.close_position(portfolio_key, proceeds)
                self.portfolio.record_snapshot(exit_ts, self.portfolio.deployed_value())
                trade_obj.fees = commission
                trade_obj.net_return = (proceeds - position_dollars) / position_dollars
                self.leaderboard.record_closed_trade(trade_obj.politician_id, exit_ts, trade_obj.net_return)

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

            if strategy.politician_selection_mode == PoliticianSelectionMode.ROLLING_LEADERBOARD:
                eligible, reason = self.leaderboard.eligibility(candidate.politician_id, entry_ts)
                if not eligible:
                    trade.excluded_reason = reason
                    results.append(trade)
                    continue
            elif strategy.politician_selection_mode == PoliticianSelectionMode.NAMED_CASE_STUDY:
                if candidate.politician_id not in strategy.named_case_study_politician_ids:
                    trade.excluded_reason = "EXCLUDED_NOT_IN_CASE_STUDY_LIST"
                    results.append(trade)
                    continue

            if candidate.instrument_kind == "OPTION" and strategy.instrument_scope.value == "stock_etf_only":
                trade.excluded_reason = "EXCLUDED_OPTIONS_NOT_IN_SCOPE"
                results.append(trade)
                continue

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

            multiplier = priced["contract_multiplier"]
            quantity = position_dollars / (priced["entry_fill"] * multiplier)
            trade.shares = quantity
            trade.position_value = position_dollars

            portfolio_key = object()
            self.portfolio.open_position(portfolio_key, candidate.ticker, quantity, position_dollars, entry_ts)
            self.portfolio.record_snapshot(entry_ts, self.portfolio.deployed_value())

            heapq.heappush(
                pending_closes,
                (trade.exit_timestamp, next(tie_breaker), portfolio_key, trade, quantity, position_dollars, priced["exit_fill"], multiplier),
            )

            results.append(trade)

        _settle_up_to(dt.datetime.max.replace(tzinfo=dt.timezone.utc))
        return results
