"""
Statistical significance testing (spec section 18). A high headline return is
never treated as sufficient evidence on its own -- see classify.py, which
requires these results before calling anything better than "weak edge."
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def bootstrap_mean_ci(returns: np.ndarray, n_resamples: int = 10_000, ci: float = 0.95, seed: int = 42) -> dict:
    returns = np.asarray(returns, dtype=float)
    returns = returns[~np.isnan(returns)]
    if len(returns) < 2:
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": len(returns)}
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    n = len(returns)
    for i in range(n_resamples):
        sample = rng.choice(returns, size=n, replace=True)
        means[i] = sample.mean()
    alpha = 1 - ci
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"mean": float(returns.mean()), "ci_low": float(lo), "ci_high": float(hi), "n": n, "n_resamples": n_resamples}


def one_sample_ttest(returns: np.ndarray) -> dict:
    returns = np.asarray(returns, dtype=float)
    returns = returns[~np.isnan(returns)]
    if len(returns) < 2:
        return {"t_stat": float("nan"), "p_value": float("nan"), "n": len(returns)}
    t_stat, p_value = stats.ttest_1samp(returns, popmean=0.0)
    return {"t_stat": float(t_stat), "p_value": float(p_value), "n": len(returns)}


def win_rate_wilson_ci(wins: int, n: int, ci: float = 0.95) -> dict:
    """Wilson score interval -- doesn't require an extra dependency (e.g.
    statsmodels) and behaves better than a normal approximation near 0/1."""
    if n == 0:
        return {"win_rate": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0}
    z = stats.norm.ppf(1 - (1 - ci) / 2)
    phat = wins / n
    denom = 1 + z**2 / n
    center = (phat + z**2 / (2 * n)) / denom
    margin = (z * np.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2))) / denom
    return {"win_rate": phat, "ci_low": max(0.0, center - margin), "ci_high": min(1.0, center + margin), "n": n}


def compare_to_null_distribution(observed_mean: float, null_means: np.ndarray) -> dict:
    """Empirical p-value: fraction of the null (e.g. randomized-entry
    benchmark) distribution at least as extreme as the observed strategy
    mean. This is the more direct test of whether the *congressional signal*
    specifically adds value, vs. generic market drift (spec section 17-18)."""
    null_means = np.asarray(null_means, dtype=float)
    null_means = null_means[~np.isnan(null_means)]
    if len(null_means) == 0:
        return {"empirical_p_value": float("nan"), "null_mean": float("nan"), "null_std": float("nan"), "n_null": 0}
    empirical_p = float(np.mean(null_means >= observed_mean))
    return {
        "empirical_p_value": empirical_p,
        "null_mean": float(null_means.mean()),
        "null_std": float(null_means.std()),
        "n_null": len(null_means),
    }
