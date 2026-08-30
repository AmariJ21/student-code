"""
Rule-based viability classification (spec section 25). Deliberately NOT a
single-metric threshold on total return -- requires out-of-sample
performance, statistical significance against two different nulls (zero and
randomized-entry), survival of a realistic slippage assumption, and
robustness across the parameter grid before calling anything better than
"weak edge." Every input and the resulting reasoning is returned alongside
the label so a reader can check the classifier's work, not just trust it.
"""

from __future__ import annotations

from enum import Enum


class Viability(str, Enum):
    NOT_VIABLE = "NOT VIABLE"
    WEAK_EDGE = "WEAK EDGE"
    PROMISING = "PROMISING"
    STRONG_HISTORICAL_EDGE = "STRONG HISTORICAL EDGE"
    INSUFFICIENT_DATA = "INSUFFICIENT DATA"
    CASE_STUDY_NOT_GENERALIZABLE = "CASE STUDY (NOT A GENERAL-EDGE CLAIM)"


MIN_SAMPLE_SIZE = 30


def classify_viability(
    sample_size: int,
    oos_mean_return: float | None,
    oos_profit_factor: float | None,
    ttest_p_value: float | None,
    empirical_p_value_vs_random: float | None,
    max_drawdown: float | None,
    excess_return_over_spy: float | None,
    net_return_survives_high_slippage: bool | None,
    robust_across_param_grid_fraction: float | None,
    is_case_study: bool = False,
) -> dict:
    if is_case_study:
        # Named-individual case-study runs (e.g. "just Pelosi") select
        # politicians BECAUSE we already know how they performed -- that's
        # hindsight, not a backtest, no matter how good the numbers look.
        # This is enforced here, not left to report-copy discipline: no
        # metric combination can escalate a case study past this label.
        return {
            "label": Viability.CASE_STUDY_NOT_GENERALIZABLE.value,
            "reasons": [
                "This run restricts candidates to specific named politicians chosen because their performance is "
                "already known -- that is hindsight/survivorship selection, not an out-of-sample test. The metrics "
                "in this report describe what would have happened following that specific, already-known-successful "
                "person; they are not evidence that following congressional disclosures in general has an edge. "
                "See PoliticianSelectionMode.ROLLING_LEADERBOARD for the methodologically honest version of "
                "'follow top performers.'"
            ],
        }

    reasons: list[str] = []

    if sample_size < MIN_SAMPLE_SIZE:
        reasons.append(f"Sample size ({sample_size}) is below the minimum ({MIN_SAMPLE_SIZE}) for any reliability claim.")
        return {"label": Viability.INSUFFICIENT_DATA.value, "reasons": reasons}

    if oos_mean_return is None or oos_profit_factor is None:
        reasons.append("Out-of-sample results are missing -- cannot classify without them (spec section 19).")
        return {"label": Viability.INSUFFICIENT_DATA.value, "reasons": reasons}

    if oos_mean_return <= 0 or oos_profit_factor < 1.0:
        reasons.append(f"Out-of-sample mean return ({oos_mean_return:.4f}) or profit factor ({oos_profit_factor:.2f}) is non-positive/unprofitable.")
        return {"label": Viability.NOT_VIABLE.value, "reasons": reasons}

    significant = (ttest_p_value is not None and ttest_p_value < 0.05) or (
        empirical_p_value_vs_random is not None and empirical_p_value_vs_random < 0.05
    )
    weakly_significant = (ttest_p_value is not None and ttest_p_value < 0.10) or (
        empirical_p_value_vs_random is not None and empirical_p_value_vs_random < 0.10
    )

    if not weakly_significant:
        reasons.append(
            f"Not statistically distinguishable from zero or from randomized entry "
            f"(t-test p={ttest_p_value}, vs-random p={empirical_p_value_vs_random})."
        )
        return {"label": Viability.NOT_VIABLE.value, "reasons": reasons}

    if excess_return_over_spy is not None and excess_return_over_spy <= 0:
        reasons.append("No excess return over a SPY buy-and-hold benchmark over the same windows (spec 16.F) -- likely market drift, not a congressional signal.")

    extreme_drawdown = max_drawdown is not None and max_drawdown < -0.50

    if (
        significant
        and net_return_survives_high_slippage
        and robust_across_param_grid_fraction is not None
        and robust_across_param_grid_fraction >= 0.6
        and (excess_return_over_spy is None or excess_return_over_spy > 0)
        and not extreme_drawdown
        and oos_profit_factor >= 1.2
    ):
        reasons.append("Statistically significant, profitable out-of-sample, survives realistic slippage, robust across most of the parameter grid, and beats a SPY benchmark.")
        return {"label": Viability.STRONG_HISTORICAL_EDGE.value, "reasons": reasons}

    if weakly_significant and (net_return_survives_high_slippage or robust_across_param_grid_fraction and robust_across_param_grid_fraction >= 0.4):
        reasons.append("Some statistical support and partial robustness, but does not clear the bar for a strong edge (see criteria above).")
        return {"label": Viability.PROMISING.value, "reasons": reasons}

    reasons.append("Positive out-of-sample return but weak/inconsistent statistical support or fails under realistic frictions/parameter sensitivity.")
    return {"label": Viability.WEAK_EDGE.value, "reasons": reasons}
