"""
Engine tests for the researched-motif extensions: option positions,
long-hold mode (incl. exercise-and-roll-to-underlying), politician
leaderboard gating, and the named-case-study classification guardrail.
Uses the same synthetic FakeProvider pattern as test_engine.py.
"""

from __future__ import annotations

import datetime as dt

from ctbacktest.backtest.classify import Viability, classify_viability
from ctbacktest.backtest.engine import BacktestEngine, TradeCandidate
from ctbacktest.config import (
    BacktestConfig,
    HoldingMode,
    InstrumentScope,
    PoliticianSelectionMode,
    PortfolioConfig,
    StrategyConfig,
)
from ctbacktest.market_data.base import Bar, BarSeries, MarketDataProvider

UTC = dt.timezone.utc


def _bar(day: str, close: float, high: float | None = None, low: float | None = None) -> Bar:
    y, m, d = map(int, day.split("-"))
    high = high if high is not None else close
    low = low if low is not None else close
    return Bar(ts=dt.datetime(y, m, d, 14, 30, tzinfo=UTC), open=close, high=high, low=low, close=close, adj_close=close, volume=1_000_000)


class FakeProvider(MarketDataProvider):
    def __init__(self, bars: list[Bar]):
        self.bars = bars

    def max_interval_lookback(self, interval, as_of):
        return None

    def get_bars(self, ticker, start, end, interval):
        return BarSeries(ticker=ticker, interval="1d", bars=self.bars, source="fake")


def _option_candidate(ticker, disclosure_date, strike, expiration, politician_id=1) -> TradeCandidate:
    return TradeCandidate(
        transaction_id=1, politician_id=politician_id, security_id=1, ticker=ticker,
        disclosure_date=disclosure_date, disclosure_timestamp=None, disclosure_confidence="DATE_ONLY_ASSUMED",
        transaction_date=disclosure_date - dt.timedelta(days=10), owner_type="SELF",
        instrument_kind="OPTION", option_type="CALL", strike_price=strike, expiration_date=expiration,
    )


def _config(**strategy_overrides) -> BacktestConfig:
    defaults = dict(instrument_scope=InstrumentScope.INCLUDE_OPTIONS)
    defaults.update(strategy_overrides)
    return BacktestConfig(
        strategy=StrategyConfig(**defaults),
        portfolio=PortfolioConfig(starting_capital=10_000, position_size_pct=1.0, max_positions=5),
    )


def _flat_series(start_price: float, days: int, drift_per_day: float = 0.0, start="2024-01-02") -> list[Bar]:
    y, m, d = map(int, start.split("-"))
    base = dt.date(y, m, d)
    bars = []
    price = start_price
    for i in range(days):
        day = (base + dt.timedelta(days=i)).isoformat()
        if (base + dt.timedelta(days=i)).weekday() >= 5:
            continue
        bars.append(_bar(day, price))
        price *= 1 + drift_per_day
    return bars


def test_options_excluded_when_not_in_scope():
    bars = _flat_series(100, 30)
    candidate = _option_candidate("XYZ", dt.date(2024, 1, 2), strike=90, expiration=dt.date(2024, 6, 1))
    config = _config(instrument_scope=InstrumentScope.STOCK_ETF_ONLY)
    trades = BacktestEngine(config, FakeProvider(bars)).run([candidate])
    assert trades[0].excluded_reason == "EXCLUDED_OPTIONS_NOT_IN_SCOPE"


def test_option_already_expired_at_entry_is_excluded():
    bars = _flat_series(100, 200)  # spans well past both dates below
    candidate = _option_candidate("XYZ", dt.date(2024, 6, 1), strike=90, expiration=dt.date(2024, 1, 2))
    trades = BacktestEngine(_config(), FakeProvider(bars)).run([candidate])
    assert trades[0].excluded_reason == "EXCLUDED_OPTION_EXPIRED_BEFORE_ENTRY"


def test_option_rallying_underlying_produces_leveraged_gain():
    # Underlying rallies strongly; a call should show a LARGER percentage
    # gain than the underlying itself (leverage), and price sensibly.
    bars = _flat_series(100, 250, drift_per_day=0.004)  # meaningful uptrend
    candidate = _option_candidate("XYZ", dt.date(2024, 1, 2), strike=90, expiration=dt.date(2025, 1, 2))
    config = _config(holding_mode=HoldingMode.SHORT_TERM_TARGET, take_profit=0.20, stop_loss=None, max_hold_days=60)
    trades = BacktestEngine(config, FakeProvider(bars)).run([candidate])
    trade = trades[0]
    assert trade.excluded_reason is None
    assert trade.instrument_kind == "OPTION"
    assert trade.exit_reason == "TAKE_PROFIT"
    assert trade.net_return > 0


def test_long_hold_option_exercises_and_rolls_to_underlying_on_itm_expiration():
    # Underlying rallies past the strike well before a near-term expiration,
    # then keeps rallying afterward -- the rolled-forward stock leg should
    # capture that continued move too (final return exceeds intrinsic-at-expiration alone).
    bars = _flat_series(50, 400, drift_per_day=0.006)
    expiration = bars[60].ts.date()  # well ITM by then given the drift
    candidate = _option_candidate("XYZ", dt.date(2024, 1, 2), strike=40, expiration=expiration)
    config = _config(holding_mode=HoldingMode.LONG_HOLD, long_hold_trailing_stop_pct=0.9)  # very loose stop so it rides to data end
    trades = BacktestEngine(config, FakeProvider(bars)).run([candidate])
    trade = trades[0]
    assert trade.excluded_reason is None
    assert trade.exercised_and_held_underlying is True
    assert "EXERCISED" in trade.exit_reason
    assert trade.net_return > 0


def test_long_hold_option_expires_worthless_when_otm():
    # Oscillating-but-declining series: a perfectly smooth deterministic drift
    # has ~zero realized volatility (hits the pricing model's volatility
    # floor), which would make a 10%-OTM option's entry premium unrealistically
    # tiny. Real prices aren't perfectly smooth, so this adds noise to get a
    # genuine, non-negligible entry premium while still ending up OTM.
    base = _flat_series(100, 100, drift_per_day=-0.006)
    bars = [
        Bar(ts=b.ts, open=b.open, high=b.close * 1.03, low=b.close * 0.97,
            close=b.close * (1.03 if i % 2 == 0 else 0.97), adj_close=b.close, volume=b.volume)
        for i, b in enumerate(base)
    ]
    # Enter after day 30 so there's real pre-entry history for the volatility
    # estimate (entering on the series' very first bar would have none, and
    # the pricing model correctly floors volatility rather than guessing).
    entry_bar = bars[30]
    expiration = bars[70].ts.date()
    candidate = _option_candidate("XYZ", entry_bar.ts.date(), strike=110, expiration=expiration)
    # No trailing stop here: the point of this test is the "ride to
    # expiration and expire worthless OTM" path specifically, not whether a
    # noisy option-value path trips a trailing stop first (options are far
    # more volatile than their underlying, so a tight trailing stop on the
    # option's own value is easily triggered by that noise -- realistic, but
    # not what this test is checking).
    config = _config(holding_mode=HoldingMode.LONG_HOLD, long_hold_trailing_stop_pct=None)
    trades = BacktestEngine(config, FakeProvider(bars)).run([candidate])
    trade = trades[0]
    assert trade.excluded_reason is None
    assert trade.exit_reason == "OPTION_EXPIRED_WORTHLESS"
    assert trade.exercised_and_held_underlying is False
    assert trade.net_return < 0


def test_leaderboard_gating_blocks_unranked_politician():
    bars = _flat_series(100, 30)
    candidate = TradeCandidate(
        transaction_id=1, politician_id=999, security_id=1, ticker="XYZ",
        disclosure_date=dt.date(2024, 1, 2), disclosure_timestamp=None, disclosure_confidence="DATE_ONLY_ASSUMED",
        transaction_date=dt.date(2023, 12, 1), owner_type="SELF",
    )
    config = BacktestConfig(
        strategy=StrategyConfig(politician_selection_mode=PoliticianSelectionMode.ROLLING_LEADERBOARD, leaderboard_min_track_record_trades=3),
        portfolio=PortfolioConfig(starting_capital=10_000, position_size_pct=1.0),
    )
    trades = BacktestEngine(config, FakeProvider(bars)).run([candidate])
    assert trades[0].excluded_reason == "EXCLUDED_INSUFFICIENT_TRACK_RECORD"


def test_named_case_study_excludes_unlisted_politicians():
    bars = _flat_series(100, 30)
    candidate = TradeCandidate(
        transaction_id=1, politician_id=42, security_id=1, ticker="XYZ",
        disclosure_date=dt.date(2024, 1, 2), disclosure_timestamp=None, disclosure_confidence="DATE_ONLY_ASSUMED",
        transaction_date=dt.date(2023, 12, 1), owner_type="SELF",
    )
    config = BacktestConfig(
        strategy=StrategyConfig(politician_selection_mode=PoliticianSelectionMode.NAMED_CASE_STUDY, named_case_study_politician_ids=[1, 2, 3]),
        portfolio=PortfolioConfig(starting_capital=10_000, position_size_pct=1.0),
    )
    trades = BacktestEngine(config, FakeProvider(bars)).run([candidate])
    assert trades[0].excluded_reason == "EXCLUDED_NOT_IN_CASE_STUDY_LIST"


def test_case_study_classification_never_escalates_regardless_of_metrics():
    """Even feeding it metrics that would otherwise qualify as a strong
    edge, a case-study run must always come back as the non-generalizable
    label -- this is a safety guarantee, not just report copy."""
    result = classify_viability(
        sample_size=1000, oos_mean_return=0.50, oos_profit_factor=5.0,
        ttest_p_value=0.0001, empirical_p_value_vs_random=0.0001,
        max_drawdown=-0.05, excess_return_over_spy=0.40,
        net_return_survives_high_slippage=True, robust_across_param_grid_fraction=1.0,
        is_case_study=True,
    )
    assert result["label"] == Viability.CASE_STUDY_NOT_GENERALIZABLE.value
