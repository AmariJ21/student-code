"""
Local dashboard (spec section 22). Deliberately the "simple HTML/JS" option
the spec allows, rather than a full React SPA, kept in a single FastAPI app
so `uvicorn ctbacktest.dashboard.app:app` is all that's needed to run it.

Pages: Overview, Equity Curve, Drawdown, Trades, Politicians, Research.
All queries read directly from Postgres (no separate cache) -- this is a
research tool for occasional local use, not a production analytics service.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from ctbacktest.db.models import BacktestRun, BacktestTrade, Politician, PortfolioSnapshot, Security
from ctbacktest.db.session import session_scope

app = FastAPI(title="Congressional Trading Backtest Dashboard")

NAV = """
<nav style="padding:1rem;background:#222;color:white;">
  <a href="/" style="color:white;margin-right:1rem;">Overview</a>
  <a href="/equity" style="color:white;margin-right:1rem;">Equity Curve</a>
  <a href="/drawdown" style="color:white;margin-right:1rem;">Drawdown</a>
  <a href="/trades" style="color:white;margin-right:1rem;">Trades</a>
  <a href="/politicians" style="color:white;margin-right:1rem;">Politicians</a>
  <a href="/research" style="color:white;margin-right:1rem;">Research</a>
</nav>
"""

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:sans-serif;margin:0;background:#fafafa;}} .content{{padding:1.5rem;}}
table{{border-collapse:collapse;width:100%;background:white;}} th,td{{border:1px solid #ddd;padding:0.4rem;font-size:0.85rem;}}
th{{background:#eee;}} .card{{display:inline-block;background:white;border:1px solid #ddd;border-radius:6px;padding:1rem;margin:0.5rem;min-width:160px;}}
select,input{{padding:0.3rem;}}</style></head><body>{nav}<div class="content">{body}</div></body></html>"""


def _latest_run_id(session) -> int | None:
    run = session.query(BacktestRun).order_by(BacktestRun.created_at.desc()).first()
    return run.run_id if run else None


def _trades_df(session, run_id: int) -> pd.DataFrame:
    return pd.read_sql(session.query(BacktestTrade).filter_by(run_id=run_id).statement, session.bind)


@app.get("/", response_class=HTMLResponse)
def overview(run_id: int | None = Query(default=None)):
    with session_scope() as session:
        run_id = run_id or _latest_run_id(session)
        if run_id is None:
            return PAGE.format(title="Overview", nav=NAV, body="<h1>No backtest runs found. Run <code>python main.py backtest</code> first.</h1>")
        df = _trades_df(session, run_id)
        sim = df[df["excluded_reason"].isna()]
        returns = sim["net_return"].dropna()
        total_return = float((1 + returns).prod() - 1) if len(returns) else 0.0
        win_rate = float((returns > 0).mean()) if len(returns) else 0.0
        winners, losers = returns[returns > 0], returns[returns <= 0]
        profit_factor = float(winners.sum() / -losers.sum()) if losers.sum() < 0 else float("inf")

        cards = "".join(
            f'<div class="card"><div>{label}</div><div style="font-size:1.4rem;font-weight:bold;">{value}</div></div>'
            for label, value in [
                ("Total Trades", len(sim)),
                ("Total Return", f"{total_return*100:.2f}%"),
                ("Win Rate", f"{win_rate*100:.1f}%"),
                ("Profit Factor", f"{profit_factor:.2f}"),
                ("Excluded Candidates", int(df["excluded_reason"].notna().sum())),
            ]
        )
        body = f"<h1>Run #{run_id} Overview</h1>{cards}"
        return PAGE.format(title="Overview", nav=NAV, body=body)


@app.get("/equity", response_class=HTMLResponse)
def equity(run_id: int | None = Query(default=None)):
    with session_scope() as session:
        run_id = run_id or _latest_run_id(session)
        snaps = pd.read_sql(session.query(PortfolioSnapshot).filter_by(run_id=run_id).statement, session.bind) if run_id else pd.DataFrame()
        fig = go.Figure()
        if not snaps.empty:
            fig.add_trace(go.Scatter(x=snaps["ts"], y=snaps["equity"], mode="lines"))
        fig.update_layout(title="Equity Curve", height=500)
        return PAGE.format(title="Equity Curve", nav=NAV, body=fig.to_html(full_html=False, include_plotlyjs="cdn"))


@app.get("/drawdown", response_class=HTMLResponse)
def drawdown(run_id: int | None = Query(default=None)):
    with session_scope() as session:
        run_id = run_id or _latest_run_id(session)
        snaps = pd.read_sql(session.query(PortfolioSnapshot).filter_by(run_id=run_id).statement, session.bind) if run_id else pd.DataFrame()
        fig = go.Figure()
        if not snaps.empty:
            fig.add_trace(go.Scatter(x=snaps["ts"], y=snaps["drawdown"] * 100, mode="lines", fill="tozeroy"))
        fig.update_layout(title="Drawdown (%)", height=500)
        return PAGE.format(title="Drawdown", nav=NAV, body=fig.to_html(full_html=False, include_plotlyjs="cdn"))


@app.get("/trades", response_class=HTMLResponse)
def trades(run_id: int | None = Query(default=None), sort_by: str = "entry_timestamp"):
    with session_scope() as session:
        run_id = run_id or _latest_run_id(session)
        df = _trades_df(session, run_id) if run_id else pd.DataFrame()
        politicians = pd.read_sql(session.query(Politician).statement, session.bind)
        securities = pd.read_sql(session.query(Security).statement, session.bind)
        if not df.empty:
            df = df.merge(politicians[["politician_id", "full_name"]], on="politician_id", how="left")
            df = df.merge(securities[["security_id", "ticker"]], on="security_id", how="left")
            cols = ["full_name", "ticker", "entry_timestamp", "exit_timestamp", "net_return", "holding_period_days", "exit_reason"]
            if sort_by in df.columns:
                df = df.sort_values(sort_by, ascending=False)
            table = df[cols].head(500).to_html(index=False)
        else:
            table = "<p>No trades to show.</p>"
        return PAGE.format(title="Trades", nav=NAV, body=f"<h1>Trades (Run #{run_id})</h1>{table}")


@app.get("/politicians", response_class=HTMLResponse)
def politicians_page(run_id: int | None = Query(default=None)):
    from ctbacktest.analysis.breakdowns import by_politician

    with session_scope() as session:
        run_id = run_id or _latest_run_id(session)
        df = _trades_df(session, run_id) if run_id else pd.DataFrame()
        politicians = pd.read_sql(session.query(Politician).statement, session.bind)
        table = by_politician(df, politicians).to_html(index=False) if not df.empty else "<p>No data.</p>"
        return PAGE.format(title="Politicians", nav=NAV, body=f"<h1>Politician Leaderboard (Run #{run_id})</h1>{table}")


@app.get("/research", response_class=HTMLResponse)
def research(
    run_id: int | None = Query(default=None),
    politician: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    owner: str | None = Query(default=None),
):
    with session_scope() as session:
        run_id = run_id or _latest_run_id(session)
        df = _trades_df(session, run_id) if run_id else pd.DataFrame()
        politicians = pd.read_sql(session.query(Politician).statement, session.bind)
        securities = pd.read_sql(session.query(Security).statement, session.bind)
        if not df.empty:
            df = df.merge(politicians[["politician_id", "full_name"]], on="politician_id", how="left")
            df = df.merge(securities[["security_id", "ticker"]], on="security_id", how="left")
            if politician:
                df = df[df["full_name"].str.contains(politician, case=False, na=False)]
            if ticker:
                df = df[df["ticker"].str.contains(ticker.upper(), case=False, na=False)]
            table = df.head(500).to_html(index=False)
        else:
            table = "<p>No data.</p>"
        form = f"""
        <form method="get">
          <input name="run_id" value="{run_id or ''}" placeholder="run_id">
          <input name="politician" value="{politician or ''}" placeholder="politician contains...">
          <input name="ticker" value="{ticker or ''}" placeholder="ticker contains...">
          <button type="submit">Filter</button>
        </form>
        """
        return PAGE.format(title="Research", nav=NAV, body=f"<h1>Research Filters</h1>{form}{table}")
