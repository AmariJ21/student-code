import datetime as dt

from ctbacktest.utils.market_calendar import add_trading_days, earliest_tradable_timestamp, is_market_open_at, next_market_open_at_or_after


def test_earliest_tradable_timestamp_during_session_is_unchanged():
    # A Senate filing at 1:43pm ET on a normal trading day (2024-01-16) --
    # the market is already open, so the trader could act immediately.
    ts = dt.datetime(2024, 1, 16, 18, 43, tzinfo=dt.timezone.utc)  # 1:43pm ET
    assert earliest_tradable_timestamp(ts) == ts


def test_earliest_tradable_timestamp_rolls_forward_past_holiday():
    # MLK Day 2024-01-15 evening -> should roll to the next trading day's open (1/16).
    ts = dt.datetime(2024, 1, 15, 23, 0, tzinfo=dt.timezone.utc)
    result = earliest_tradable_timestamp(ts)
    assert result.date() == dt.date(2024, 1, 16)
    assert is_market_open_at(result)


def test_earliest_tradable_timestamp_rolls_forward_past_weekend():
    saturday = dt.datetime(2024, 1, 20, 12, 0, tzinfo=dt.timezone.utc)
    result = earliest_tradable_timestamp(saturday)
    assert result.date() == dt.date(2024, 1, 22)  # the following Monday


def test_next_market_open_always_advances_even_mid_session():
    mid_session = dt.datetime(2024, 1, 16, 16, 0, tzinfo=dt.timezone.utc)
    result = next_market_open_at_or_after(mid_session)
    assert result.date() == dt.date(2024, 1, 17)


def test_add_trading_days_skips_weekend():
    tuesday_open = next_market_open_at_or_after(dt.datetime(2024, 1, 16, 0, 0, tzinfo=dt.timezone.utc))
    result = add_trading_days(tuesday_open, 5)
    # Tue,Wed,Thu,Fri,(weekend skipped),Mon,Tue = 5 trading days later is the following Tuesday.
    assert result.date() == dt.date(2024, 1, 23)
