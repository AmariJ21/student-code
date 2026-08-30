"""
Wires the DB, market data layer, and backtest engine together into the
operations the CLI exposes. Kept separate from cli/main.py so the dashboard
(or a notebook) can reuse the same functions without going through argparse.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from ctbacktest.backtest import benchmarks, metrics as metrics_mod, statistics as stats_mod
from ctbacktest.backtest.classify import classify_viability
from ctbacktest.backtest.engine import BacktestEngine, TradeCandidate
from ctbacktest.analysis import attribution, breakdowns
from ctbacktest.config import BacktestConfig, TransactionType, AssetType
from ctbacktest.db.models import BacktestRun, BacktestTrade, Disclosure, PortfolioSnapshot, Politician, Security, Transaction

logger = logging.getLogger(__name__)

# Options backtesting requires a materially different engine (strike/expiry-
# aware); out of scope for this project (spec section 8: "do not blindly
# treat options as ordinary stock purchases"). Only equity-like instruments
# are simulated -- everything else is excluded and counted, not dropped.
SIMULATABLE_ASSET_TYPES = {AssetType.COMMON_STOCK.value, AssetType.ETF.value}


def load_candidates_from_db(
    session, start_date: dt.date | None = None, end_date: dt.date | None = None, buys_only: bool = True
) -> list[TradeCandidate]:
    query = (
        session.query(Transaction, Disclosure, Security)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.disclosure_id)
        .outerjoin(Security, Transaction.security_id == Security.security_id)
    )
    if buys_only:
        query = query.filter(Transaction.transaction_type == TransactionType.BUY.value)
    if start_date:
        query = query.filter(Disclosure.disclosure_date >= start_date)
    if end_date:
        query = query.filter(Disclosure.disclosure_date <= end_date)

    candidates = []
    skipped_asset_type = 0
    skipped_no_ticker = 0
    for txn, disclosure, security in query.all():
        if security is None or not security.ticker:
            skipped_no_ticker += 1
            continue
        if txn.asset_type not in SIMULATABLE_ASSET_TYPES:
            skipped_asset_type += 1
            continue
        candidates.append(
            TradeCandidate(
                transaction_id=txn.transaction_id,
                politician_id=disclosure.politician_id,
                security_id=security.security_id,
                ticker=security.ticker,
                disclosure_date=disclosure.disclosure_date,
                disclosure_timestamp=disclosure.disclosure_timestamp,
                disclosure_confidence=disclosure.disclosure_confidence,
                transaction_date=txn.transaction_date,
                owner_type=txn.owner_type,
                amount_min=txn.amount_min,
                amount_max=txn.amount_max,
                expected_asset_name=security.asset_name,
            )
        )
    logger.info(
        "Loaded %d simulatable BUY candidates (skipped %d non-equity/option/bond asset types, %d with no resolvable ticker).",
        len(candidates),
        skipped_asset_type,
        skipped_no_ticker,
    )
    return candidates


def persist_run(session, config: BacktestConfig, trades, portfolio, split_label: str, data_version: str | None = None) -> int:
    run = BacktestRun(
        strategy_version=config.strategy_version,
        configuration_hash=config.config_hash(),
        configuration_json=config.model_dump(mode="json"),
        data_version=data_version,
        split_label=split_label,
    )
    session.add(run)
    session.flush()

    for t in trades:
        session.add(
            BacktestTrade(
                run_id=run.run_id,
                transaction_id=t.transaction_id,
                politician_id=t.politician_id,
                security_id=t.security_id,
                disclosure_delay_days=t.disclosure_delay_days,
                entry_timestamp=t.entry_timestamp or dt.datetime.now(dt.timezone.utc),
                entry_price=t.entry_price or 0.0,
                shares=t.shares or 0.0,
                position_value=t.position_value or 0.0,
                target_price=t.target_price or 0.0,
                stop_price=t.stop_price,
                exit_timestamp=t.exit_timestamp,
                exit_price=t.exit_price,
                exit_reason=t.exit_reason,
                gross_return=t.gross_return,
                slippage_cost=t.slippage_cost,
                fees=t.fees,
                net_return=t.net_return,
                holding_period_days=t.holding_period_days,
                mfe=t.mfe,
                mae=t.mae,
                price_resolution=t.price_resolution,
                disclosure_confidence=t.disclosure_confidence,
                ambiguous_same_bar=t.ambiguous_same_bar,
                excluded_reason=t.excluded_reason,
            )
        )
    for ts, equity, cash, open_positions, drawdown in portfolio.equity_snapshots:
        session.add(
            PortfolioSnapshot(
                run_id=run.run_id, ts=ts, equity=equity, cash=cash, open_positions=open_positions, drawdown=drawdown
            )
        )
    session.flush()
    return run.run_id


def run_full_backtest(
    session,
    config: BacktestConfig,
    market_data,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    run_benchmarks: bool = True,
    run_bootstrap: bool = True,
) -> dict:
    candidates = load_candidates_from_db(session, start_date, end_date)
    engine = BacktestEngine(config, market_data)
    trades = engine.run(candidates)
    df = metrics_mod.trades_to_dataframe(trades)

    bundle: dict = {
        "config": config,
        "candidates": candidates,
        "trades": trades,
        "trades_df": df,
        "portfolio": engine.portfolio,
        "exclusion_summary": metrics_mod.exclusion_summary(df),
        "return_metrics": metrics_mod.return_metrics(df),
        "winloss_metrics": metrics_mod.winloss_metrics(df),
        "risk_metrics": metrics_mod.risk_metrics(engine.portfolio.equity_snapshots),
        "strategy_metrics": metrics_mod.strategy_specific_metrics(df),
    }

    sim_returns = df.loc[df["excluded_reason"].isna(), "net_return"].dropna().values if not df.empty else []
    if run_bootstrap and len(sim_returns) > 1:
        bundle["bootstrap_ci"] = stats_mod.bootstrap_mean_ci(sim_returns)
        bundle["ttest"] = stats_mod.one_sample_ttest(sim_returns)
        wins = int((sim_returns > 0).sum())
        bundle["win_rate_ci"] = stats_mod.win_rate_wilson_ci(wins, len(sim_returns))

    politicians = pd.read_sql(session.query(Politician).statement, session.bind)
    securities = pd.read_sql(session.query(Security).statement, session.bind)
    transactions = pd.read_sql(session.query(Transaction).statement, session.bind)

    if not df.empty:
        bundle["by_politician"] = breakdowns.by_politician(df, politicians)
        bundle["by_chamber"] = breakdowns.by_chamber(df, politicians)
        merged_txn = df.merge(transactions[["transaction_id", "owner_type", "amount_min"]], on="transaction_id", how="left")
        bundle["by_owner"] = breakdowns.by_owner(merged_txn, merged_txn["owner_type"])
        bundle["by_transaction_size"] = breakdowns.by_transaction_size(merged_txn, merged_txn["amount_min"])
        bundle["by_sector"] = breakdowns.by_sector(df, securities)
        bundle["by_market_cap"] = breakdowns.by_market_cap_bucket(df, securities)
        bundle["by_disclosure_delay"] = breakdowns.by_disclosure_delay(df)

        if not bundle["by_politician"].empty:
            bundle["politician_concentration"] = attribution.concentration_by_group(bundle["by_politician"])
        if not bundle["by_sector"].empty:
            bundle["sector_concentration"] = attribution.concentration_by_group(bundle["by_sector"])

    if run_benchmarks and trades:
        spy_returns = benchmarks.spy_buy_hold_benchmark(trades, market_data)
        bundle["spy_benchmark_returns"] = spy_returns
        strategy_mean = float(sim_returns.mean()) if len(sim_returns) else float("nan")
        bundle["market_wide_check"] = attribution.market_wide_momentum_check(strategy_mean, spy_returns)

    return bundle


def classify_from_bundle(bundle: dict, oos_bundle: dict | None = None, robustness_bundle: dict | None = None) -> dict:
    """Uses out-of-sample results when available (oos_bundle), falling back
    to the full-sample bundle with a note -- classify.py itself treats
    missing OOS results as INSUFFICIENT_DATA, so this is only a convenience
    wrapper that decides which bundle's numbers to feed in."""
    source = oos_bundle or bundle
    sim_df = source["trades_df"]
    n = int(sim_df["excluded_reason"].isna().sum()) if not sim_df.empty else 0
    mean_return = source["return_metrics"].get("average_trade_return")
    profit_factor = source["winloss_metrics"].get("profit_factor")
    p_value = source.get("ttest", {}).get("p_value")
    empirical_p = None  # filled in by caller if a randomized-entry benchmark was run
    max_dd = source["risk_metrics"].get("max_drawdown")
    excess_spy = source.get("market_wide_check", {}).get("excess_over_spy")

    survives_slippage = None
    robust_fraction = None
    if robustness_bundle:
        slip_results = robustness_bundle.get("slippage_sweep", [])
        high_slip = [r for r in slip_results if r.get("entry_exit_slippage", 0) >= 0.005]
        if high_slip:
            survives_slippage = all(r.get("average_trade_return", -1) is not None and r["average_trade_return"] > 0 for r in high_slip)
        grid_results = robustness_bundle.get("take_profit_sweep", []) + robustness_bundle.get("max_hold_days_sweep", [])
        if grid_results:
            positive = sum(1 for r in grid_results if (r.get("average_trade_return") or -1) > 0)
            robust_fraction = positive / len(grid_results)

    return classify_viability(
        sample_size=n,
        oos_mean_return=mean_return,
        oos_profit_factor=profit_factor,
        ttest_p_value=p_value,
        empirical_p_value_vs_random=empirical_p,
        max_drawdown=max_dd,
        excess_return_over_spy=excess_spy,
        net_return_survives_high_slippage=survives_slippage,
        robust_across_param_grid_fraction=robust_fraction,
    )
