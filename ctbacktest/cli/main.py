"""
CLI entry points (spec section 23): ingest / update / backtest / analyze /
report / optimize. Each command loads .env (if present), ensures the DB
schema exists (idempotent), and wires the DB + market-data layer together via
ctbacktest/orchestration.py.
"""

from __future__ import annotations

import datetime as dt
import logging

import click
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _session_scope():
    from ctbacktest.db.init_db import init_db
    from ctbacktest.db.session import session_scope

    init_db()
    return session_scope()


def _market_data():
    from ctbacktest.market_data.cache import CachingProvider
    from ctbacktest.market_data.yfinance_provider import YFinanceProvider

    return CachingProvider(YFinanceProvider())


@click.group()
def cli():
    """Congressional Trading Disclosure Backtesting System (research only -- see FEASIBILITY.md)."""


@cli.command()
@click.option("--source", type=click.Choice(["legislators", "senate", "house", "backfill"]), required=True)
@click.option("--start", type=str, help="YYYY-MM-DD (senate)")
@click.option("--end", type=str, help="YYYY-MM-DD (senate)")
@click.option("--year", type=int, help="Filing year (house)")
def ingest(source, start, end, year):
    """One-time or historical-range ingestion for a given source."""
    with _session_scope() as session:
        if source == "legislators":
            from ctbacktest.ingestion.legislators import upsert_legislators

            n = upsert_legislators(session)
            click.echo(f"Upserted {n} politician records from unitedstates/congress-legislators.")
        elif source == "senate":
            from ctbacktest.ingestion.pipeline import ingest_senate_range

            if not start or not end:
                raise click.UsageError("--start and --end are required for --source senate")
            stats = ingest_senate_range(session, dt.date.fromisoformat(start), dt.date.fromisoformat(end))
            click.echo(f"Senate ingest: {stats}")
        elif source == "house":
            from ctbacktest.ingestion.pipeline import ingest_house_year

            if not year:
                raise click.UsageError("--year is required for --source house")
            stats = ingest_house_year(session, year)
            click.echo(f"House ingest: {stats}")
        elif source == "backfill":
            click.echo(
                "Backfill loader fetches supplemental community data but does not "
                "auto-write to the DB in this CLI to avoid silently mixing "
                "confidence levels -- see ingestion/stock_watcher_backfill.py "
                "and wire it up explicitly if you need it."
            )


@cli.command()
@click.option("--days", default=14, help="Trailing window (days) to re-poll for new filings.")
def update(days):
    """Re-run ingestion over a recent trailing window (for cron/incremental use)."""
    with _session_scope() as session:
        from ctbacktest.ingestion.pipeline import ingest_senate_range, ingest_house_year

        end = dt.date.today()
        start = end - dt.timedelta(days=days)
        stats_senate = ingest_senate_range(session, start, end)
        stats_house = ingest_house_year(session, end.year)
        click.echo(f"Senate: {stats_senate}")
        click.echo(f"House (current year, full-year re-check): {stats_house}")


@cli.command()
@click.option("--take-profit", type=float, default=0.10)
@click.option("--stop-loss", type=float, default=None)
@click.option("--max-hold-days", type=int, default=30)
@click.option("--entry-delay-minutes", default=0, help="int minutes, or 'next_open'")
@click.option("--start-date", type=str, default=None)
@click.option("--end-date", type=str, default=None)
@click.option("--split-label", type=str, default="full")
@click.option("--starting-capital", type=float, default=10_000.0)
@click.option("--position-size-pct", type=float, default=0.10)
@click.option("--max-positions", type=int, default=10)
@click.option("--with-benchmarks/--no-benchmarks", default=True)
@click.option("--with-robustness/--no-robustness", default=False, help="Run the full robustness grid (slower: re-runs the engine many times).")
def backtest(
    take_profit, stop_loss, max_hold_days, entry_delay_minutes, start_date, end_date, split_label,
    starting_capital, position_size_pct, max_positions, with_benchmarks, with_robustness,
):
    """Run one backtest, persist it, and write results/backtest_<run_id>/."""
    from ctbacktest.config import BacktestConfig, ExecutionConfig, PortfolioConfig, StrategyConfig
    from ctbacktest.orchestration import classify_from_bundle, persist_run, run_full_backtest
    from ctbacktest.backtest.robustness import full_robustness_report
    from ctbacktest.reporting.report_builder import build_report

    delay = entry_delay_minutes if entry_delay_minutes == "next_open" else int(entry_delay_minutes)
    config = BacktestConfig(
        strategy=StrategyConfig(take_profit=take_profit, stop_loss=stop_loss, max_hold_days=max_hold_days, entry_delay_minutes=delay),
        execution=ExecutionConfig(),
        portfolio=PortfolioConfig(starting_capital=starting_capital, position_size_pct=position_size_pct, max_positions=max_positions),
        start_date=start_date,
        end_date=end_date,
        split_label=split_label,
    )

    sd = dt.date.fromisoformat(start_date) if start_date else None
    ed = dt.date.fromisoformat(end_date) if end_date else None

    with _session_scope() as session:
        market_data = _market_data()
        bundle = run_full_backtest(session, config, market_data, sd, ed, run_benchmarks=with_benchmarks)
        if with_robustness:
            bundle["robustness"] = full_robustness_report(bundle["candidates"], config, market_data)
        classification = classify_from_bundle(bundle, robustness_bundle=bundle.get("robustness"))
        run_id = persist_run(session, config, bundle["trades"], bundle["portfolio"], split_label)
        out_dir = build_report(run_id, bundle, classification)
        click.echo(f"Run #{run_id} ({classification['label']}) -- report written to {out_dir}")


@cli.command()
@click.option("--run-id", type=int, required=True)
def analyze(run_id):
    """Print stored breakdown analysis for an existing run."""
    from ctbacktest.db.models import BacktestTrade
    import pandas as pd

    with _session_scope() as session:
        df = pd.read_sql(session.query(BacktestTrade).filter_by(run_id=run_id).statement, session.bind)
        if df.empty:
            click.echo(f"No trades found for run_id={run_id}.")
            return
        click.echo(df.describe(include="all").to_string())


@cli.command()
@click.option("--run-id", type=int, required=True)
def report(run_id):
    """Regenerate the results/backtest_<run_id>/ artifact bundle from what's stored in the DB."""
    from ctbacktest.db.models import BacktestRun, BacktestTrade
    from ctbacktest.backtest.engine import SimulatedTrade
    from ctbacktest.backtest.metrics import exclusion_summary, return_metrics, risk_metrics, strategy_specific_metrics, trades_to_dataframe, winloss_metrics
    from ctbacktest.backtest.portfolio import PortfolioState
    from ctbacktest.config import BacktestConfig
    from ctbacktest.orchestration import classify_from_bundle
    from ctbacktest.reporting.report_builder import build_report

    with _session_scope() as session:
        run = session.query(BacktestRun).filter_by(run_id=run_id).one_or_none()
        if run is None:
            raise click.UsageError(f"No such run_id={run_id}")
        db_trades = session.query(BacktestTrade).filter_by(run_id=run_id).all()
        trades = [
            SimulatedTrade(
                transaction_id=t.transaction_id, politician_id=t.politician_id, security_id=t.security_id,
                ticker="", disclosure_delay_days=t.disclosure_delay_days, entry_timestamp=t.entry_timestamp,
                entry_price=t.entry_price, shares=t.shares, position_value=t.position_value,
                target_price=t.target_price, stop_price=t.stop_price, exit_timestamp=t.exit_timestamp,
                exit_price=t.exit_price, exit_reason=t.exit_reason, gross_return=t.gross_return,
                slippage_cost=t.slippage_cost, fees=t.fees, net_return=t.net_return,
                holding_period_days=t.holding_period_days, mfe=t.mfe, mae=t.mae,
                price_resolution=t.price_resolution, disclosure_confidence=t.disclosure_confidence,
                ambiguous_same_bar=t.ambiguous_same_bar, excluded_reason=t.excluded_reason,
            )
            for t in db_trades
        ]
        config = BacktestConfig(**run.configuration_json)
        df = trades_to_dataframe(trades)
        portfolio = PortfolioState(config=config.portfolio)
        bundle = {
            "config": config, "trades": trades, "trades_df": df, "portfolio": portfolio,
            "exclusion_summary": exclusion_summary(df), "return_metrics": return_metrics(df),
            "winloss_metrics": winloss_metrics(df), "risk_metrics": risk_metrics(portfolio.equity_snapshots),
            "strategy_metrics": strategy_specific_metrics(df),
        }
        classification = classify_from_bundle(bundle)
        out_dir = build_report(run_id, bundle, classification)
        click.echo(f"Report regenerated at {out_dir}")


@cli.command()
@click.option("--start-date", type=str, required=True)
@click.option("--end-date", type=str, required=True)
def optimize(start_date, end_date):
    """Run the predefined TP/SL/hold-day experiment matrix on train+validation
    data only (spec section 19/23) -- never on the held-out test split."""
    from ctbacktest.config import ALLOWED_MAX_HOLD_DAYS, ALLOWED_STOP_LOSSES, ALLOWED_TAKE_PROFITS, BacktestConfig, StrategyConfig
    from ctbacktest.backtest.walk_forward import train_validation_test_split
    from ctbacktest.orchestration import load_candidates_from_db, run_full_backtest

    sd, ed = dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)
    splits = train_validation_test_split(sd, ed)
    train, validation = splits[0], splits[1]
    click.echo(f"Optimizing on {train.label} [{train.start}, {train.end}] + {validation.label} [{validation.start}, {validation.end}]; "
               f"test split [{splits[2].start}, {splits[2].end}] is untouched.")

    with _session_scope() as session:
        market_data = _market_data()
        results = []
        for tp in ALLOWED_TAKE_PROFITS:
            for sl in ALLOWED_STOP_LOSSES:
                for hold in ALLOWED_MAX_HOLD_DAYS:
                    config = BacktestConfig(strategy=StrategyConfig(take_profit=tp, stop_loss=sl, max_hold_days=hold))
                    bundle = run_full_backtest(session, config, market_data, train.start, validation.end, run_benchmarks=False, run_bootstrap=False)
                    row = {"take_profit": tp, "stop_loss": sl, "max_hold_days": hold, **bundle["return_metrics"], **bundle["winloss_metrics"]}
                    results.append(row)
        import pandas as pd

        results_df = pd.DataFrame(results).sort_values("average_trade_return", ascending=False)
        click.echo(results_df.head(15).to_string())
        results_df.to_csv("results/optimize_grid.csv", index=False)
        click.echo("Full grid written to results/optimize_grid.csv")


if __name__ == "__main__":
    cli()
