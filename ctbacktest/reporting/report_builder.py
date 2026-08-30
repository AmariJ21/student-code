"""
Writes the per-run results/backtest_<run_id>/ artifact bundle (spec section
24): summary.json, trades.csv, equity_curve.csv, politician_analysis.csv,
sector_analysis.csv, disclosure_delay.csv, and a self-contained report.html
covering the 16 required sections, including a Limitations section that
always restates the data limitations from FEASIBILITY.md rather than
presenting the numbers as if the ideal experiment had been run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _json_default(obj):
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    try:
        import numpy as np

        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    return str(obj)


def _equity_curve_df(portfolio) -> pd.DataFrame:
    return pd.DataFrame(portfolio.equity_snapshots, columns=["ts", "equity", "cash", "open_positions", "drawdown"])


def _fig_equity_curve(equity_df: pd.DataFrame) -> str:
    fig = go.Figure()
    if not equity_df.empty:
        fig.add_trace(go.Scatter(x=equity_df["ts"], y=equity_df["equity"], mode="lines", name="Equity"))
    fig.update_layout(title="Portfolio Equity Curve (event-driven snapshots)", xaxis_title="Time", yaxis_title="Equity ($)", height=400)
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def _fig_drawdown(equity_df: pd.DataFrame) -> str:
    fig = go.Figure()
    if not equity_df.empty:
        fig.add_trace(go.Scatter(x=equity_df["ts"], y=equity_df["drawdown"] * 100, mode="lines", name="Drawdown %", fill="tozeroy"))
    fig.update_layout(title="Drawdown Over Time", xaxis_title="Time", yaxis_title="Drawdown (%)", height=350)
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _fig_return_distribution(trades_df: pd.DataFrame) -> str:
    fig = go.Figure()
    if not trades_df.empty and "net_return" in trades_df:
        sim = trades_df[trades_df["excluded_reason"].isna()]
        fig.add_trace(go.Histogram(x=sim["net_return"] * 100, nbinsx=40, name="Net return %"))
    fig.update_layout(title="Distribution of Trade Returns", xaxis_title="Net Return (%)", yaxis_title="Count", height=350)
    return fig.to_html(full_html=False, include_plotlyjs=False)


def build_report(run_id: int, bundle: dict, classification: dict, output_root: str | Path = "results") -> Path:
    output_dir = Path(output_root) / f"backtest_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    trades_df = bundle["trades_df"]
    equity_df = _equity_curve_df(bundle["portfolio"])

    trades_df.to_csv(output_dir / "trades.csv", index=False)
    equity_df.to_csv(output_dir / "equity_curve.csv", index=False)
    for key, filename in [
        ("by_politician", "politician_analysis.csv"),
        ("by_sector", "sector_analysis.csv"),
        ("by_disclosure_delay", "disclosure_delay.csv"),
    ]:
        df = bundle.get(key)
        if df is not None and not df.empty:
            df.to_csv(output_dir / filename, index=False)

    config = bundle["config"]
    summary = {
        "run_id": run_id,
        "configuration": config.model_dump(mode="json"),
        "configuration_hash": config.config_hash(),
        "exclusion_summary": bundle.get("exclusion_summary"),
        "return_metrics": bundle.get("return_metrics"),
        "winloss_metrics": bundle.get("winloss_metrics"),
        "risk_metrics": bundle.get("risk_metrics"),
        "strategy_metrics": bundle.get("strategy_metrics"),
        "bootstrap_ci": bundle.get("bootstrap_ci"),
        "ttest": bundle.get("ttest"),
        "win_rate_ci": bundle.get("win_rate_ci"),
        "market_wide_check": bundle.get("market_wide_check"),
        "politician_concentration": bundle.get("politician_concentration"),
        "sector_concentration": bundle.get("sector_concentration"),
        "classification": classification,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=_json_default))

    def _table_html(df: pd.DataFrame | None, max_rows: int = 30) -> str:
        if df is None or df.empty:
            return "<p><em>No data available for this breakdown.</em></p>"
        return df.head(max_rows).to_html(index=False, classes="data-table", float_format=lambda v: f"{v:.4f}")

    robustness_tables = {}
    for key, label in [
        ("slippage_sweep", "entry_exit_slippage"),
        ("entry_delay_sweep", "entry_delay_minutes"),
        ("take_profit_sweep", "take_profit"),
        ("stop_loss_sweep", "stop_loss"),
        ("max_hold_days_sweep", "max_hold_days"),
    ]:
        rows = bundle.get("robustness", {}).get(key) if bundle.get("robustness") else None
        robustness_tables[key] = _table_html(pd.DataFrame(rows)) if rows else None

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]))
    template = env.get_template("report.html.j2")
    html = template.render(
        run_id=run_id,
        summary=summary,
        equity_fig=_fig_equity_curve(equity_df),
        drawdown_fig=_fig_drawdown(equity_df),
        return_dist_fig=_fig_return_distribution(trades_df),
        by_politician=_table_html(bundle.get("by_politician")),
        by_sector=_table_html(bundle.get("by_sector")),
        by_chamber=_table_html(bundle.get("by_chamber")),
        by_owner=_table_html(bundle.get("by_owner")),
        by_transaction_size=_table_html(bundle.get("by_transaction_size")),
        by_market_cap=_table_html(bundle.get("by_market_cap")),
        by_disclosure_delay=_table_html(bundle.get("by_disclosure_delay")),
        robustness_tables=robustness_tables,
    )
    (output_dir / "report.html").write_text(html)
    return output_dir
