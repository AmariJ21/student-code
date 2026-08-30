import datetime as dt

from ctbacktest.backtest.portfolio import PortfolioState
from ctbacktest.config import PortfolioConfig, PositionSizingMode


def _now():
    return dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)


def test_percent_of_equity_sizing():
    p = PortfolioState(config=PortfolioConfig(starting_capital=10_000, position_size_pct=0.10))
    size = p.size_position(current_equity=10_000)
    assert size == 1_000


def test_cannot_exceed_max_positions():
    p = PortfolioState(config=PortfolioConfig(starting_capital=10_000, position_size_pct=0.10, max_positions=2))
    p.open_position("a", "AAPL", 1, 1000, _now())
    p.open_position("b", "MSFT", 1, 1000, _now())
    assert p.can_open_position(current_equity=10_000) is False


def test_cannot_exceed_max_exposure():
    p = PortfolioState(config=PortfolioConfig(starting_capital=10_000, position_size_pct=0.50, max_positions=10, max_portfolio_exposure=0.5))
    p.open_position("a", "AAPL", 1, 5000, _now())
    assert p.can_open_position(current_equity=10_000) is False


def test_fixed_dollar_sizing_capped_by_cash():
    p = PortfolioState(config=PortfolioConfig(
        starting_capital=1_000, position_sizing_mode=PositionSizingMode.FIXED_DOLLAR, fixed_dollar_amount=5_000
    ))
    # Can't size a position bigger than available cash even if fixed_dollar_amount asks for more.
    assert p.size_position(current_equity=1_000) <= 1_000


def test_equal_weight_sizing_divides_by_max_positions():
    p = PortfolioState(config=PortfolioConfig(
        starting_capital=10_000, position_sizing_mode=PositionSizingMode.EQUAL_WEIGHT, max_positions=10
    ))
    assert p.size_position(current_equity=10_000) == 1_000


def test_close_position_returns_cash_and_frees_slot():
    p = PortfolioState(config=PortfolioConfig(starting_capital=10_000, position_size_pct=0.10, max_positions=1))
    p.open_position("a", "AAPL", 10, 1000, _now())
    assert p.cash == 9_000
    assert p.can_open_position(10_000) is False
    p.close_position("a", proceeds=1100)
    assert p.cash == 10_100
    assert len(p.open_positions) == 0


def test_drawdown_tracking():
    p = PortfolioState(config=PortfolioConfig(starting_capital=10_000))
    p.record_snapshot(_now(), 0)
    assert p.equity_snapshots[-1][1] == 10_000
    p.cash = 12_000
    p.record_snapshot(_now(), 0)
    p.cash = 9_000
    p.record_snapshot(_now(), 0)
    last_drawdown = p.equity_snapshots[-1][4]
    assert last_drawdown < 0
    assert abs(last_drawdown - (9_000 - 12_000) / 12_000) < 1e-9
