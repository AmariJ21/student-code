from ctbacktest.backtest.classify import MIN_SAMPLE_SIZE, Viability, classify_viability


def _base_kwargs(**overrides):
    kwargs = dict(
        sample_size=100,
        oos_mean_return=0.03,
        oos_profit_factor=1.5,
        ttest_p_value=0.01,
        empirical_p_value_vs_random=0.02,
        max_drawdown=-0.10,
        excess_return_over_spy=0.02,
        net_return_survives_high_slippage=True,
        robust_across_param_grid_fraction=0.8,
    )
    kwargs.update(overrides)
    return kwargs


def test_insufficient_data_below_minimum_sample():
    result = classify_viability(**_base_kwargs(sample_size=MIN_SAMPLE_SIZE - 1))
    assert result["label"] == Viability.INSUFFICIENT_DATA.value


def test_not_viable_when_unprofitable_out_of_sample():
    result = classify_viability(**_base_kwargs(oos_mean_return=-0.01))
    assert result["label"] == Viability.NOT_VIABLE.value


def test_not_viable_when_not_statistically_significant():
    result = classify_viability(**_base_kwargs(ttest_p_value=0.5, empirical_p_value_vs_random=0.5))
    assert result["label"] == Viability.NOT_VIABLE.value


def test_strong_edge_requires_all_criteria():
    result = classify_viability(**_base_kwargs())
    assert result["label"] == Viability.STRONG_HISTORICAL_EDGE.value


def test_high_return_alone_does_not_imply_strong_edge():
    # A juicy mean return with no significance and no robustness must not
    # be classified as a strong edge just because the number looks good.
    result = classify_viability(**_base_kwargs(
        oos_mean_return=0.50,
        ttest_p_value=0.4,
        empirical_p_value_vs_random=0.4,
        net_return_survives_high_slippage=False,
        robust_across_param_grid_fraction=0.1,
    ))
    assert result["label"] != Viability.STRONG_HISTORICAL_EDGE.value


def test_strong_edge_downgraded_by_extreme_drawdown():
    result = classify_viability(**_base_kwargs(max_drawdown=-0.75))
    assert result["label"] != Viability.STRONG_HISTORICAL_EDGE.value


def test_promising_when_partially_robust():
    result = classify_viability(**_base_kwargs(
        ttest_p_value=0.08,
        empirical_p_value_vs_random=0.09,
        robust_across_param_grid_fraction=0.5,
        net_return_survives_high_slippage=False,
    ))
    assert result["label"] == Viability.PROMISING.value
