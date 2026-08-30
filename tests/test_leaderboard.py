import datetime as dt

from ctbacktest.backtest.leaderboard import PoliticianLeaderboard

UTC = dt.timezone.utc


def _ts(day):
    return dt.datetime(2024, 1, day, tzinfo=UTC)


def test_no_track_record_is_ineligible():
    board = PoliticianLeaderboard(min_track_record_trades=3, top_k=10)
    eligible, reason = board.eligibility(politician_id=1, as_of_ts=_ts(1))
    assert eligible is False
    assert reason == "EXCLUDED_INSUFFICIENT_TRACK_RECORD"


def test_becomes_eligible_after_enough_closed_trades():
    board = PoliticianLeaderboard(min_track_record_trades=3, top_k=10)
    for day, ret in [(1, 0.05), (2, 0.03), (3, 0.02)]:
        board.record_closed_trade(politician_id=1, exit_ts=_ts(day), net_return=ret)
    eligible, _ = board.eligibility(politician_id=1, as_of_ts=_ts(10))
    assert eligible is True


def test_future_trades_never_leak_into_past_eligibility_check():
    """The core causality guarantee: a politician's trade that closes AFTER
    `as_of_ts` must not count toward their track record at that time."""
    board = PoliticianLeaderboard(min_track_record_trades=1, top_k=10)
    board.record_closed_trade(politician_id=1, exit_ts=_ts(20), net_return=0.50)  # closes in the "future" relative to day 5
    eligible, reason = board.eligibility(politician_id=1, as_of_ts=_ts(5))
    assert eligible is False
    assert reason == "EXCLUDED_INSUFFICIENT_TRACK_RECORD"


def test_top_k_excludes_lower_ranked_politicians():
    board = PoliticianLeaderboard(min_track_record_trades=1, top_k=1)
    board.record_closed_trade(politician_id=1, exit_ts=_ts(1), net_return=0.20)  # best performer
    board.record_closed_trade(politician_id=2, exit_ts=_ts(1), net_return=0.01)  # worst performer
    eligible_1, _ = board.eligibility(politician_id=1, as_of_ts=_ts(10))
    eligible_2, reason_2 = board.eligibility(politician_id=2, as_of_ts=_ts(10))
    assert eligible_1 is True
    assert eligible_2 is False
    assert reason_2 == "EXCLUDED_NOT_IN_LEADERBOARD_TOP_K"


def test_lookback_window_drops_stale_trades():
    board = PoliticianLeaderboard(lookback_days=30, min_track_record_trades=1, top_k=10)
    board.record_closed_trade(politician_id=1, exit_ts=_ts(1), net_return=0.10)
    far_future = _ts(1) + dt.timedelta(days=200)
    eligible, reason = board.eligibility(politician_id=1, as_of_ts=far_future)
    assert eligible is False
    assert reason == "EXCLUDED_INSUFFICIENT_TRACK_RECORD"


def test_ranked_politicians_sorted_descending_by_score():
    board = PoliticianLeaderboard(min_track_record_trades=1, top_k=10)
    board.record_closed_trade(politician_id=1, exit_ts=_ts(1), net_return=0.05)
    board.record_closed_trade(politician_id=2, exit_ts=_ts(1), net_return=0.20)
    board.record_closed_trade(politician_id=3, exit_ts=_ts(1), net_return=-0.10)
    ranked = board.ranked_politicians(as_of_ts=_ts(10))
    assert [r.politician_id for r in ranked] == [2, 1, 3]
