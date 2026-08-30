# Implementation Plan

## Architecture

```
congressional_backtest/
  ctbacktest/
    config.py               # StrategyConfig/ExecutionConfig/PortfolioConfig, YAML loading, config hashing
    db/
      models.py             # SQLAlchemy ORM models (see schema below)
      session.py            # engine/session factory (Postgres via DATABASE_URL)
      init_db.py            # create_all + seed helper
    ingestion/
      legislators.py        # unitedstates/congress-legislators -> Politician rows
      senate_efd.py          # official Senate eFD scraper + PTR parser
      house_clerk.py         # official House Clerk scraper + PTR parser
      stock_watcher_backfill.py  # optional supplemental JSON loader (clearly tagged, staleness warning)
      normalize.py           # raw filing dict -> common Transaction schema, owner/asset classification
    market_data/
      base.py                # MarketDataProvider ABC, Bar/BarSeries dataclasses
      yfinance_provider.py
      polygon_provider.py     # optional, requires POLYGON_API_KEY
      cache.py                # Parquet-backed local cache keyed by (ticker, interval, range)
      corporate_actions.py    # split/dividend adjustment notes + ticker alias resolution
    backtest/
      config.py               # enums for TP/SL/hold-day grids, validation
      engine.py                # event-driven loop; the only module allowed to open/close positions
      execution.py             # slippage/spread/fee model
      portfolio.py             # capital tracking, position sizing modes, exposure caps
      same_bar.py              # ambiguity resolution (conservative default / strict mode)
      metrics.py               # returns/risk/win-loss/strategy-specific metrics
      statistics.py            # bootstrap CI, t-test, p-value, win-rate CI
      benchmarks.py            # SPY buy-hold, txn-date buy, disclosure-date buy, randomized entry
      walk_forward.py          # train/validation/test split + walk-forward windows
      robustness.py            # slippage/delay/param sweeps reusing engine
      classify.py              # rule-based viability classification
    analysis/
      breakdowns.py            # groupby aggregations (politician/chamber/owner/size/sector/mktcap/delay)
      attribution.py           # concentration diagnostics (Congress-wide vs few politicians/sectors/etc.)
    reporting/
      report_builder.py        # writes results/backtest_<run_id>/* incl. report.html
      templates/report.html.j2
    dashboard/
      app.py                   # FastAPI app, 6 pages, Jinja2 + Plotly.js (CDN)
      templates/*.html.j2
    cli/
      main.py                  # ingest / update / backtest / analyze / report / optimize
  tests/                       # offline, synthetic-data unit tests
  docker-compose.yml           # Postgres 16
  requirements.txt / pyproject.toml
  .env.example
  README.md
```

## Database schema (PostgreSQL)

- **politicians**(politician_id PK, bioguide_id UNIQUE, full_name, chamber, party, state, district, first_seen, last_seen)
- **securities**(security_id PK, ticker, asset_name, asset_type, sector, market_cap_bucket_asof, first_seen, last_seen, is_delisted)
- **disclosures**(disclosure_id PK, politician_id FK, chamber, filing_id UNIQUE, disclosure_date, disclosure_timestamp, disclosure_confidence, source, source_url, raw_document_ref, ingested_at)
- **transactions**(transaction_id PK, disclosure_id FK, security_id FK NULLABLE, ticker_raw, asset_type, transaction_type, owner_type, transaction_date, amount_min, amount_max, parse_confidence)
- **price_bars**(price_bar_id PK, security_id FK, ts, interval, open, high, low, close, adj_close, volume, source, UNIQUE(security_id, ts, interval))
- **backtest_runs**(run_id PK, strategy_version, configuration_hash, configuration_json, data_version, split_label, created_at)
- **backtest_trades**(trade_id PK, run_id FK, transaction_id FK, politician_id FK, security_id FK, disclosure_delay_days, entry_timestamp, entry_price, shares, position_value, target_price, stop_price, exit_timestamp, exit_price, exit_reason, gross_return, slippage_cost, fees, net_return, holding_period_days, mfe, mae, price_resolution, disclosure_confidence, ambiguous_same_bar)
- **portfolio_snapshots**(snapshot_id PK, run_id FK, ts, equity, cash, open_positions, drawdown)

`configuration_hash` = sha256 of the canonicalized strategy/execution/portfolio config JSON, so two runs are only considered "the same experiment" if every parameter matches — this is the reproducibility guarantee from the spec.

## Data pipeline

`ingest` → legislators (bootstrap politicians) → senate_efd/house_clerk (disclosures+transactions, upsert by filing_id) → optional stock_watcher_backfill for gap-filling older periods, tagged `source='stock_watcher_backfill'` and never overwriting an officially-sourced row.

`update` → same ingestion functions restricted to a recent date window, run repeatedly (e.g. cron) to build a live `SCRAPER_OBSERVED` disclosure-timestamp feed going forward.

Market data is fetched lazily by the backtester (per ticker/date range actually needed) through the cache, not eagerly for the whole market.

## Backtesting methodology

1. For each BUY transaction's disclosure, compute `market_entry_timestamp` per §5 of FEASIBILITY.md.
2. Pull the best-available bar series from `market_entry_timestamp` forward (interval chosen by the fallback chain: 1m → 5m → daily, bounded by what the provider can actually return for that date).
3. Walk bars forward; apply TP/SL/max-hold rules; resolve same-bar ambiguity per `same_bar.py`; apply execution model (spread+slippage+fees) at both entry and exit.
4. Position sizing and portfolio constraints are enforced by `portfolio.py` (percent-of-equity default, configurable fixed/equal-weight/max-exposure/max-positions) — the engine cannot open a trade the portfolio model rejects.
5. Every trade and every equity/drawdown snapshot is persisted under the run's `run_id`.

## Validation methodology

- **Train/validation/test split** by disclosure date (default cut points chosen from actually-ingested data range, documented per run — no hard-coded dates assumed valid regardless of what was ingested).
- **Walk-forward**: rolling re-evaluation of a fixed, small predefined parameter grid (from the spec's TP/SL/hold-day lists) — `optimize` never searches outside that grid and never touches the held-out test split until a final confirmatory run.
- **Benchmarks** computed on the identical trade timestamps/holding periods so comparisons are apples-to-apples.
- **Statistical testing**: bootstrap resampling of trade returns (default 10,000 resamples) for CI on mean return, one-sample t-test vs. zero, and a binomial CI on win rate; randomized-entry benchmark provides an empirical null distribution as a second, more direct test of whether the *congressional signal specifically* (vs. generic momentum/market drift) adds value.
- **Robustness**: same engine re-run across the slippage/delay/parameter grids from the spec; results are compared, not just the base case.

## Testing strategy

Unit tests use hand-built synthetic bar series and disclosures (no network) to verify: the engine never reads a bar timestamped before `market_entry_timestamp`; same-bar ambiguity is flagged/handled per mode; position sizing respects capital and max-position caps; metrics/statistics functions are correct against hand-computed expected values; the classifier's rule table produces the expected label at known boundary cases. Ingestion/market-data integration is validated by the user against live sources when they run it locally (network access to senate.gov/house.gov/Yahoo Finance is required and not assumed available in every execution environment).
