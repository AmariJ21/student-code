"""
Full engine tests against a synthetic, offline MarketDataProvider -- no
network calls. Complements the live validation already done manually against
real Yahoo/Senate data (see commit history); these pin down the exact
behavior with fully controlled inputs so regressions are caught automatically.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ctbacktest.backtest.engine import BacktestEngine, TradeCandidate, compute_market_entry_timestamp
from ctbacktest.config import BacktestConfig, PortfolioConfig, SameBarMode, StrategyConfig
from ctbacktest.market_data.base import Bar, BarSeries, MarketDataProvider

UTC = dt.timezone.utc


def _bar(day: str, o, h, l, c, hour=14, minute=30) -> Bar:
    y, m, d = map(int, day.split("-"))
    return Bar(ts=dt.datetime(y, m, d, hour, minute, tzinfo=UTC), open=o, high=h, low=l, close=c, adj_close=c, volume=1_000_000)


class FakeProvider(MarketDataProvider):
    """Serves a fixed, hand-authored bar series per ticker regardless of the
    requested range -- the engine is responsible for not looking at bars
    before its entry timestamp, and this fake makes it easy to prove that."""

    def __init__(self, series_by_ticker: dict[str, list[Bar]], name_by_ticker: dict[str, str] | None = None):
        self.series_by_ticker = series_by_ticker
        self.name_by_ticker = name_by_ticker or {}
        self.requested_starts: list[dt.datetime] = []

    def max_interval_lookback(self, interval, as_of):
        return None  # unlimited, like daily

    def get_bars(self, ticker, start, end, interval):
        self.requested_starts.append(start)
        bars = self.series_by_ticker.get(ticker, [])
        return BarSeries(ticker=ticker, interval="1d", bars=bars, source="fake", security_name=self.name_by_ticker.get(ticker))


def _candidate(ticker, disclosure_date, disclosure_timestamp=None, confidence="DATE_ONLY_ASSUMED", asset_name=None) -> TradeCandidate:
    return TradeCandidate(
        transaction_id=1,
        politician_id=1,
        security_id=1,
        ticker=ticker,
        disclosure_date=disclosure_date,
        disclosure_timestamp=disclosure_timestamp,
        disclosure_confidence=confidence,
        transaction_date=disclosure_date - dt.timedelta(days=20),
        owner_type="SELF",
        expected_asset_name=asset_name,
    )


def _config(**strategy_overrides) -> BacktestConfig:
    defaults = dict(take_profit=0.10, stop_loss=None, max_hold_days=30)
    defaults.update(strategy_overrides)
    return BacktestConfig(
        strategy=StrategyConfig(**defaults),
        portfolio=PortfolioConfig(starting_capital=10_000, position_size_pct=1.0, max_positions=1),
    )


def test_take_profit_exit():
    series = [
        _bar("2024-01-16", 100, 101, 99, 100),
        _bar("2024-01-17", 100, 112, 100, 108),  # target (110) not reached yet
        _bar("2024-01-18", 108, 111, 107, 109),  # touches 110 intrabar -> TAKE_PROFIT
    ]
    provider = FakeProvider({"AAPL": series})
    candidate = _candidate("AAPL", dt.date(2024, 1, 16))
    trades = BacktestEngine(_config(), provider).run([candidate])
    assert trades[0].exit_reason == "TAKE_PROFIT"
    assert trades[0].excluded_reason is None
    assert trades[0].exit_price is not None and trades[0].exit_price < 110  # exit slippage makes the fill worse than the raw target


def test_stop_loss_exit():
    series = [
        _bar("2024-01-16", 100, 101, 99, 100),
        _bar("2024-01-17", 100, 101, 89, 95),  # touches stop (90) intrabar
    ]
    provider = FakeProvider({"AAPL": series})
    candidate = _candidate("AAPL", dt.date(2024, 1, 16))
    trades = BacktestEngine(_config(stop_loss=0.10), provider).run([candidate])
    assert trades[0].exit_reason == "STOP_LOSS"


def test_time_exit_when_nothing_triggers():
    series = [_bar("2024-01-16", 100, 101, 99, 100)] + [
        _bar((dt.date(2024, 1, 17) + dt.timedelta(days=i)).isoformat(), 100, 102, 99, 101) for i in range(60)
    ]
    provider = FakeProvider({"AAPL": series})
    candidate = _candidate("AAPL", dt.date(2024, 1, 16))
    trades = BacktestEngine(_config(max_hold_days=5), provider).run([candidate])
    assert trades[0].exit_reason == "TIME_EXIT"


def test_same_bar_conservative_resolves_against_the_trade():
    series = [
        _bar("2024-01-16", 100, 101, 99, 100),
        _bar("2024-01-17", 100, 115, 85, 100),  # both target (110) and stop (90) touched in one bar
    ]
    provider = FakeProvider({"AAPL": series})
    candidate = _candidate("AAPL", dt.date(2024, 1, 16))
    trades = BacktestEngine(_config(stop_loss=0.10, same_bar_mode=SameBarMode.CONSERVATIVE), provider).run([candidate])
    assert trades[0].exit_reason == "STOP_LOSS"


def test_same_bar_strict_mode_excludes_from_headline_but_still_reports():
    series = [
        _bar("2024-01-16", 100, 101, 99, 100),
        _bar("2024-01-17", 100, 115, 85, 100),
    ]
    provider = FakeProvider({"AAPL": series})
    candidate = _candidate("AAPL", dt.date(2024, 1, 16))
    trades = BacktestEngine(_config(stop_loss=0.10, same_bar_mode=SameBarMode.STRICT_AMBIGUOUS), provider).run([candidate])
    assert trades[0].exit_reason == "AMBIGUOUS_SAME_BAR"
    assert trades[0].ambiguous_same_bar is True


def test_no_price_data_is_excluded_not_dropped():
    provider = FakeProvider({})  # no data for any ticker
    candidate = _candidate("ZZZZ", dt.date(2024, 1, 16))
    trades = BacktestEngine(_config(), provider).run([candidate])
    assert len(trades) == 1  # never silently dropped
    assert trades[0].excluded_reason == "EXCLUDED_NO_PRICE_DATA"
    assert trades[0].entry_price is None


def test_ticker_identity_mismatch_is_excluded():
    series = [_bar("2024-01-16", 100, 101, 99, 100), _bar("2024-01-17", 100, 112, 99, 108)]
    provider = FakeProvider({"PARA": series}, name_by_ticker={"PARA": "Banzai International, Inc."})
    candidate = _candidate("PARA", dt.date(2024, 1, 16), asset_name="Paramount Global Cl B")
    trades = BacktestEngine(_config(), provider).run([candidate])
    assert trades[0].excluded_reason == "EXCLUDED_TICKER_IDENTITY_MISMATCH"


def test_engine_never_reads_bars_before_entry_timestamp():
    """The core look-ahead invariant (spec section 4): entry must key off
    disclosure, and no bar before the resolved entry timestamp should ever
    factor into MFE/MAE or the exit decision."""
    series = [
        _bar("2024-01-10", 100, 500, 1, 100),  # huge favorable AND adverse excursion BEFORE disclosure -- must be ignored
        _bar("2024-01-16", 100, 101, 99, 100),
        _bar("2024-01-17", 100, 103, 98, 102),
    ]
    provider = FakeProvider({"AAPL": series})
    candidate = _candidate("AAPL", dt.date(2024, 1, 16))
    trades = BacktestEngine(_config(), provider).run([candidate])
    trade = trades[0]
    # If the pre-disclosure bar leaked in, MFE would be enormous (500 vs entry ~100).
    assert trade.mfe is not None and trade.mfe < 1.0


def test_mid_session_exact_disclosure_uses_close_and_resumes_next_bar():
    """A Senate filing at 1:43pm ET (mid-session) with only daily bars
    available: the day's open already happened before disclosure, so entry
    must use that day's close, and the exit-checking loop must not use that
    same day's high/low (which happened partly before disclosure)."""
    series = [
        _bar("2024-01-16", 100, 200, 50, 105),  # entry day: huge range, but only the close (105) is usable
        _bar("2024-01-17", 105, 106, 104, 105.5),
    ]
    provider = FakeProvider({"AAPL": series})
    exact_ts = dt.datetime(2024, 1, 16, 18, 43, tzinfo=UTC)  # 1:43pm ET, mid-session
    candidate = _candidate("AAPL", dt.date(2024, 1, 16), disclosure_timestamp=exact_ts, confidence="EXACT")
    trades = BacktestEngine(_config(), provider).run([candidate])
    trade = trades[0]
    # entry_price should derive from close (~105), not open (100) or the day's extremes.
    assert 100 < trade.entry_price < 115
    # MFE must not reflect the entry-day's 200 high, which happened before disclosure.
    assert trade.mfe < 0.05


def test_compute_market_entry_timestamp_never_uses_transaction_date():
    candidate = _candidate("AAPL", dt.date(2024, 1, 16))
    candidate.transaction_date = dt.date(1999, 1, 1)  # sabotage: if this leaked in, the result would be wildly wrong
    entry_ts = compute_market_entry_timestamp(candidate)
    assert entry_ts.year == 2024


def test_portfolio_capacity_excludes_second_candidate():
    series = [_bar("2024-01-16", 100, 101, 99, 100), _bar("2024-01-17", 100, 103, 98, 102)]
    provider = FakeProvider({"AAPL": series, "MSFT": series})
    config = _config()
    config.portfolio.max_positions = 1
    config.portfolio.position_size_pct = 1.0
    candidates = [
        _candidate("AAPL", dt.date(2024, 1, 16)),
        _candidate("MSFT", dt.date(2024, 1, 16)),
    ]
    trades = BacktestEngine(config, provider).run(candidates)
    excluded = [t for t in trades if t.excluded_reason is not None]
    assert len(excluded) == 1
    assert excluded[0].excluded_reason == "EXCLUDED_PORTFOLIO_CAPACITY"
