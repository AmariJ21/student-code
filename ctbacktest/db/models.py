"""
SQLAlchemy ORM models for the normalized schema described in
IMPLEMENTATION_PLAN.md. Postgres is the target database (see
db/session.py); the models use only portable SQLAlchemy types so
`create_all` also works against SQLite for quick local unit testing.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Politician(Base):
    __tablename__ = "politicians"

    politician_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bioguide_id: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    chamber: Mapped[str] = mapped_column(String(16), nullable=False)  # HOUSE / SENATE
    party: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    district: Mapped[str | None] = mapped_column(String(8), nullable=True)
    first_seen: Mapped[dt.date | None] = mapped_column(nullable=True)
    last_seen: Mapped[dt.date | None] = mapped_column(nullable=True)

    # Added for the leadership/committee motif found in research (party
    # leaders/whips/chairs show a documented post-ascension return premium;
    # committee jurisdiction is linked to informed sell-side trading). Each
    # entry: {"role": str, "committee": str|None, "start": "YYYY-MM-DD",
    # "end": "YYYY-MM-DD"|None}. Sourced from unitedstates/congress-legislators
    # committee-membership + leadership data -- see ingestion/legislators.py.
    # A JSON list rather than a join table: this project's scope doesn't
    # need relational queries across it, just point-in-time lookups.
    leadership_and_committee_history: Mapped[list | None] = mapped_column(JSON, nullable=True)

    disclosures: Mapped[list["Disclosure"]] = relationship(back_populates="politician")

    __table_args__ = (
        UniqueConstraint("bioguide_id", "chamber", name="uq_politicians_bioguide_chamber"),
    )


class Security(Base):
    __tablename__ = "securities"

    security_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    asset_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    market_cap_bucket_asof: Mapped[str | None] = mapped_column(
        String(16), nullable=True, doc="Present-day approximation; see FEASIBILITY.md #8"
    )
    ticker_aliases: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_delisted: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[dt.date | None] = mapped_column(nullable=True)
    last_seen: Mapped[dt.date | None] = mapped_column(nullable=True)

    __table_args__ = (UniqueConstraint("ticker", name="uq_securities_ticker"),)


class Disclosure(Base):
    __tablename__ = "disclosures"

    disclosure_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    politician_id: Mapped[int] = mapped_column(ForeignKey("politicians.politician_id"), nullable=False)
    chamber: Mapped[str] = mapped_column(String(16), nullable=False)
    filing_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    disclosure_date: Mapped[dt.date] = mapped_column(nullable=False)
    disclosure_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disclosure_confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="DATE_ONLY_ASSUMED")
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # senate_efd / house_clerk / stock_watcher_backfill
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_document_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    politician: Mapped["Politician"] = relationship(back_populates="disclosures")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="disclosure")


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[int] = mapped_column(ForeignKey("disclosures.disclosure_id"), nullable=False)
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.security_id"), nullable=True)
    ticker_raw: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    transaction_type: Mapped[str] = mapped_column(String(16), nullable=False)  # BUY/SELL/EXCHANGE/UNKNOWN
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    transaction_date: Mapped[dt.date] = mapped_column(nullable=False)
    amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="HIGH")

    # Populated only when asset_type == OPTION; parsed from the filing's free-text
    # description (House "D:" comment / Senate "Option Type: ... Strike price: ...
    # Expires: ..." asset-name suffix) -- see ingestion/normalize.py. Quantity/
    # contract count is deliberately NOT stored: the backtest sizes its own
    # position independently of the discloser's position size (see engine.py),
    # the same way it already does for plain stock trades.
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)  # "CALL" / "PUT"
    strike_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiration_date: Mapped[dt.date | None] = mapped_column(nullable=True)

    disclosure: Mapped["Disclosure"] = relationship(back_populates="transactions")
    security: Mapped["Security | None"] = relationship()


class PriceBar(Base):
    __tablename__ = "price_bars"

    price_bar_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.security_id"), nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)  # 1m/5m/1d
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="yfinance")

    __table_args__ = (
        UniqueConstraint("security_id", "ts", "interval", name="uq_price_bars_sec_ts_interval"),
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    configuration_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    data_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    split_label: Mapped[str] = mapped_column(String(32), nullable=False, default="full")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    trades: Mapped[list["BacktestTrade"]] = relationship(back_populates="run")
    snapshots: Mapped[list["PortfolioSnapshot"]] = relationship(back_populates="run")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    trade_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.run_id"), nullable=False)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.transaction_id"), nullable=False)
    politician_id: Mapped[int] = mapped_column(ForeignKey("politicians.politician_id"), nullable=False)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.security_id"), nullable=False)

    disclosure_delay_days: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    position_value: Mapped[float] = mapped_column(Float, nullable=False)
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    exit_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    gross_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_period_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)

    price_resolution: Mapped[str] = mapped_column(String(8), nullable=False, default="1d")
    disclosure_confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="DATE_ONLY_ASSUMED")
    ambiguous_same_bar: Mapped[bool] = mapped_column(Boolean, default=False)

    instrument_kind: Mapped[str] = mapped_column(String(8), nullable=False, default="STOCK")  # STOCK / OPTION
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    strike_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiration_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    modeled_volatility: Mapped[float | None] = mapped_column(
        Float, nullable=True, doc="Trailing realized vol used as the IV proxy at entry -- see backtest/options.py"
    )
    underlying_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    underlying_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exercised_and_held_underlying: Mapped[bool] = mapped_column(
        Boolean, default=False, doc="True if a long-hold option position was exercised at/near expiration and continued as a stock holding"
    )

    excluded_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True, doc="Set (never silently dropped) when a candidate trade could not be simulated"
    )

    run: Mapped["BacktestRun"] = relationship(back_populates="trades")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.run_id"), nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    run: Mapped["BacktestRun"] = relationship(back_populates="snapshots")
