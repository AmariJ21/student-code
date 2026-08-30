import datetime as dt

from ctbacktest.backtest.options import (
    OptionType,
    black_scholes_price,
    trailing_realized_volatility,
    year_fraction,
)
from ctbacktest.market_data.base import Bar

UTC = dt.timezone.utc


def _bar(day, close):
    return Bar(ts=dt.datetime(2024, 1, day, tzinfo=UTC), open=close, high=close, low=close, close=close, adj_close=close, volume=1)


def test_atm_call_matches_textbook_ballpark():
    price = black_scholes_price(100, 100, 1.0, 0.30, 0.05, 0.0, OptionType.CALL)
    assert 13.0 < price < 15.0  # standard textbook value for these inputs


def test_call_price_increases_with_higher_volatility():
    low_vol = black_scholes_price(100, 100, 1.0, 0.15, 0.05, 0.0, OptionType.CALL)
    high_vol = black_scholes_price(100, 100, 1.0, 0.50, 0.05, 0.0, OptionType.CALL)
    assert high_vol > low_vol


def test_at_expiration_equals_intrinsic_value():
    call = black_scholes_price(120, 100, 0.0, 0.3, 0.04, 0.0, OptionType.CALL)
    put = black_scholes_price(80, 100, 0.0, 0.3, 0.04, 0.0, OptionType.PUT)
    assert call == 20.0
    assert put == 20.0


def test_otm_option_at_expiration_is_worthless():
    call = black_scholes_price(90, 100, 0.0, 0.3, 0.04, 0.0, OptionType.CALL)
    assert call == 0.0


def test_year_fraction_basic():
    assert abs(year_fraction(dt.date(2024, 1, 1), dt.date(2025, 1, 1)) - 1.0) < 0.01


def test_year_fraction_never_negative():
    assert year_fraction(dt.date(2024, 6, 1), dt.date(2024, 1, 1)) == 0.0


def test_trailing_volatility_floors_on_sparse_data():
    bars = [_bar(1, 100.0)]
    vol = trailing_realized_volatility(bars, dt.datetime(2024, 1, 1, tzinfo=UTC))
    assert vol >= 0.05  # MIN_VOLATILITY floor


def test_trailing_volatility_reflects_real_price_swings():
    calm = [_bar(d, 100.0 + (d % 2) * 0.1) for d in range(1, 31)]
    wild = [_bar(d, 100.0 * (1.1 if d % 2 == 0 else 0.9)) for d in range(1, 31)]
    as_of = dt.datetime(2024, 1, 30, tzinfo=UTC)
    vol_calm = trailing_realized_volatility(calm, as_of)
    vol_wild = trailing_realized_volatility(wild, as_of)
    assert vol_wild > vol_calm
