from ctbacktest.backtest.same_bar import ExitTrigger, evaluate_bar
from ctbacktest.config import SameBarMode


def test_take_profit_only():
    outcome = evaluate_bar(bar_low=95, bar_high=112, target_price=110, stop_price=None, same_bar_mode=SameBarMode.CONSERVATIVE)
    assert outcome.trigger == ExitTrigger.TAKE_PROFIT
    assert outcome.fill_price == 110


def test_stop_loss_only():
    outcome = evaluate_bar(bar_low=88, bar_high=105, target_price=110, stop_price=90, same_bar_mode=SameBarMode.CONSERVATIVE)
    assert outcome.trigger == ExitTrigger.STOP_LOSS
    assert outcome.fill_price == 90


def test_neither_triggers():
    outcome = evaluate_bar(bar_low=98, bar_high=104, target_price=110, stop_price=90, same_bar_mode=SameBarMode.CONSERVATIVE)
    assert outcome.trigger == ExitTrigger.NONE
    assert outcome.fill_price is None


def test_same_bar_conservative_never_assumes_favorable_outcome():
    # Classic spec example: open=100, high=112, low=90, target=110, stop=90.
    outcome = evaluate_bar(bar_low=90, bar_high=112, target_price=110, stop_price=90, same_bar_mode=SameBarMode.CONSERVATIVE)
    assert outcome.trigger == ExitTrigger.STOP_LOSS, "conservative mode must resolve the tie against the trade, not for it"
    assert outcome.fill_price == 90


def test_same_bar_strict_mode_flags_ambiguous_instead_of_choosing():
    outcome = evaluate_bar(bar_low=90, bar_high=112, target_price=110, stop_price=90, same_bar_mode=SameBarMode.STRICT_AMBIGUOUS)
    assert outcome.trigger == ExitTrigger.AMBIGUOUS
    assert outcome.fill_price is None
