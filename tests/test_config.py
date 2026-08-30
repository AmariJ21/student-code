import pytest
from pydantic import ValidationError

from ctbacktest.config import BacktestConfig, StrategyConfig


def test_baseline_config_matches_spec_defaults():
    cfg = StrategyConfig()
    assert cfg.take_profit == 0.10
    assert cfg.stop_loss is None
    assert cfg.max_hold_days == 30


def test_take_profit_must_be_from_allowed_grid():
    with pytest.raises(ValidationError):
        StrategyConfig(take_profit=0.1234)


def test_stop_loss_must_be_from_allowed_grid():
    with pytest.raises(ValidationError):
        StrategyConfig(stop_loss=0.03)


def test_config_hash_is_deterministic():
    a = BacktestConfig()
    b = BacktestConfig()
    assert a.config_hash() == b.config_hash()


def test_config_hash_changes_with_any_parameter():
    a = BacktestConfig()
    b = BacktestConfig(strategy=StrategyConfig(take_profit=0.20))
    assert a.config_hash() != b.config_hash()
