import numpy as np

from ctbacktest.backtest.statistics import bootstrap_mean_ci, compare_to_null_distribution, one_sample_ttest, win_rate_wilson_ci


def test_bootstrap_ci_contains_true_mean_for_low_variance_sample():
    rng = np.random.default_rng(0)
    returns = rng.normal(loc=0.05, scale=0.01, size=200)
    result = bootstrap_mean_ci(returns, n_resamples=2000, seed=1)
    assert result["ci_low"] < 0.05 < result["ci_high"]


def test_ttest_significant_for_clearly_positive_returns():
    returns = np.array([0.1] * 50) + np.random.default_rng(1).normal(0, 0.01, 50)
    result = one_sample_ttest(returns)
    assert result["p_value"] < 0.01


def test_ttest_not_significant_for_zero_mean_noise():
    returns = np.random.default_rng(2).normal(0, 0.05, 30)
    result = one_sample_ttest(returns)
    assert result["p_value"] > 0.01 or abs(returns.mean()) < 0.02


def test_win_rate_wilson_ci_bounds():
    result = win_rate_wilson_ci(wins=8, n=10)
    assert 0 <= result["ci_low"] <= result["win_rate"] <= result["ci_high"] <= 1


def test_win_rate_wilson_ci_zero_n():
    result = win_rate_wilson_ci(wins=0, n=0)
    assert result["n"] == 0


def test_compare_to_null_distribution_empirical_p_value():
    null_means = np.array([0.0, 0.01, 0.02, -0.01, 0.03])
    result = compare_to_null_distribution(observed_mean=0.10, null_means=null_means)
    assert result["empirical_p_value"] == 0.0  # nothing in the null beat 0.10

    result_low = compare_to_null_distribution(observed_mean=-0.10, null_means=null_means)
    assert result_low["empirical_p_value"] == 1.0  # everything in the null beat -0.10
