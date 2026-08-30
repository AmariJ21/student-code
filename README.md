# Congressional Trading Disclosure Backtesting System

**Research / paper-trading only.** This project never places real trades,
never connects to a brokerage, and never submits live orders. It exists to
answer one question honestly: *if you bought stocks right after congressional
disclosure and sold at a target gain, would that have been a statistically
meaningful, cost-adjusted edge?* Read **[FEASIBILITY.md](FEASIBILITY.md)**
first -- it documents what the data actually supports (and doesn't) before
any of the numbers below are trustworthy. **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**
covers the architecture and schema.

## Setup

```bash
git clone <this repo> && cd <this repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set DATABASE_URL (docker-compose below gives you one), and only
# set CTBACKTEST_ACCEPT_SENATE_EFD_TERMS=true after reading
# https://efdsearch.senate.gov/search/home/'s terms of use yourself -- see
# FEASIBILITY.md #9. The Senate scraper refuses to run without it.

docker compose up -d          # local Postgres on localhost:5432
python main.py ingest --source legislators
```

(SQLite also works for local experimentation -- just set `DATABASE_URL=sqlite:///ctbacktest.db`
in `.env` instead of running docker-compose. Postgres is the target
production database per the schema in IMPLEMENTATION_PLAN.md.)

## Ingesting data

```bash
# Official Senate PTR filings for a date range (requires the terms flag above)
python main.py ingest --source senate --start 2024-01-01 --end 2024-12-31

# Official House PTR filings for a filing year
python main.py ingest --source house --year 2024

# Re-poll a trailing window (for cron/incremental use)
python main.py update --days 14
```

Ingestion hits the real government sites directly (no scraped copy is
bundled) and is deliberately rate-limited -- a multi-year backfill will take
a while. See FEASIBILITY.md for what's actually obtainable (Senate carries a
real filing timestamp; House does not) and for the House PDF parser's known
limitations (scanned pre-electronic-era filings are skipped, not OCR'd).

## Running a backtest

```bash
python main.py backtest \
    --take-profit 0.10 --max-hold-days 30 \
    --start-date 2024-01-01 --end-date 2024-12-31 \
    --with-benchmarks --with-robustness
# (omit --stop-loss for the baseline's "no stop loss"; pass e.g. --stop-loss 0.05 to set one)
```

This writes `results/backtest_<run_id>/` containing `summary.json`,
`trades.csv`, `equity_curve.csv`, `politician_analysis.csv`,
`sector_analysis.csv`, `disclosure_delay.csv`, and a self-contained
`report.html` (open it directly in a browser).

### The researched-motif options (see FEASIBILITY.md's "Restructuring" addendum)

The baseline above tests the mechanical "any disclosed BUY, flip at a fixed
target" hypothesis. Research showed the standout individual performers'
returns are driven mostly by leveraged long-dated options held for years, and
that performance is concentrated in specific people/leadership, not spread
across Congress. Three flags test that shape instead -- as a **separate**
run, never blended into the baseline's numbers:

```bash
python main.py backtest \
    --instrument-scope include_options --holding-mode long_hold \
    --politician-selection rolling_leaderboard --leaderboard-top-k 10 \
    --start-date 2020-01-01 --end-date 2025-12-31 --max-positions 40
```

- `--instrument-scope include_options` simulates parsed option trades via
  Black-Scholes (`backtest/options.py`) instead of skipping them.
- `--holding-mode long_hold` drops the fixed take-profit/max-hold-days rule
  for a trailing stop, and rolls an exercised ITM option into an
  equivalent-dollar stock position rather than closing it outright.
- `--politician-selection rolling_leaderboard` only follows politicians who
  already have a track record *as of that point in the backtest* (never
  based on performance that hasn't happened yet from that point's
  perspective) -- this is the honest version of "follow top performers."
- **Long-hold positions tie up portfolio capacity for a long time** (often
  over a year each), so `--max-positions` needs to be set deliberately
  higher than the short-term default, and the ingestion window needs to
  span years, or almost every candidate will be excluded on capacity alone
  before ever reaching a price check -- see the FEASIBILITY.md addendum for
  a real example where this excluded 92.5% of candidates.
- For an illustrative (never general-edge) look at one specific person:
  `--politician-selection named_case_study --case-study-name Pelosi`. Any
  such run is always reported as `CASE STUDY (NOT A GENERAL-EDGE CLAIM)`,
  enforced in `backtest/classify.py` regardless of how good its numbers look.

`--with-robustness` reruns the
engine across the slippage/delay/TP/SL/hold-day grids from the spec, which
is slower (many re-fetches) but is what the viability classification (also
in the report) actually depends on -- a single base-case run alone will
generally land as `INSUFFICIENT_DATA` or an under-supported classification.

```bash
python main.py optimize --start-date 2024-01-01 --end-date 2024-12-31
```

runs the predefined TP/SL/hold-day grid on the train+validation split only
(never the held-out test split) and writes `results/optimize_grid.csv`.

```bash
python main.py report --run-id 3     # regenerate a report from what's stored in the DB
python main.py analyze --run-id 3    # quick stats dump for a stored run
```

## Dashboard

```bash
uvicorn ctbacktest.dashboard.app:app --reload
```

Then open http://127.0.0.1:8000/ for Overview, Equity Curve, Drawdown,
Trades, Politicians, and Research pages (pass `?run_id=N` to view a specific
run; defaults to the most recent).

## Tests

```bash
pytest
```

54 offline tests covering the engine, portfolio, statistics, classifier,
and filing-text normalization against synthetic data -- no network or
database required. Ingestion and market-data modules were validated live
against the real Senate/House/Yahoo endpoints during development (see the
git history for specifics); they need a live network connection to exercise
directly since there's no bundled fixture data for those.

## What "viable" actually means here

The system never calls a strategy `STRONG HISTORICAL EDGE` off a good total
return alone -- see `ctbacktest/backtest/classify.py`. It requires
out-of-sample performance, statistical significance against two different
nulls (zero and a randomized-entry benchmark), survival under a realistic
slippage assumption, and robustness across the parameter grid. Read the
"Limitations" and "Final Conclusion" sections of any generated `report.html`
before drawing conclusions from the headline numbers.
