"""
Orchestrates the scrapers in ingestion/senate_efd.py and ingestion/house_clerk.py
into the normalized DB schema (Politician/Disclosure/Transaction/Security).

Idempotent by design: Disclosure.filing_id is unique, so re-running an ingest
over a date range that was already loaded skips filings already present
rather than duplicating them.
"""

from __future__ import annotations

import datetime as dt
import logging

from ctbacktest.config import AssetType, OwnerType, TransactionType
from ctbacktest.db.models import Disclosure, Politician, Security, Transaction
from ctbacktest.ingestion import house_clerk, normalize, senate_efd

logger = logging.getLogger(__name__)


def _get_or_create_politician(session, first_name: str, last_name: str, chamber: str) -> Politician:
    full_name = f"{first_name} {last_name}".strip()
    existing = (
        session.query(Politician)
        .filter(Politician.chamber == chamber)
        .filter(Politician.full_name.ilike(f"%{last_name}%"))
        .filter(Politician.full_name.ilike(f"%{first_name.split()[0]}%") if first_name else True)
        .first()
    )
    if existing:
        return existing
    logger.info("No legislator-roster match for %s (%s); creating minimal Politician record.", full_name, chamber)
    politician = Politician(bioguide_id=None, full_name=full_name, chamber=chamber)
    session.add(politician)
    session.flush()
    return politician


def _get_or_create_security(session, ticker: str, asset_name: str | None, asset_type: AssetType) -> Security:
    existing = session.query(Security).filter_by(ticker=ticker).one_or_none()
    if existing:
        return existing
    security = Security(ticker=ticker, asset_name=asset_name, asset_type=asset_type.value)
    session.add(security)
    session.flush()
    return security


def ingest_senate_range(session, start_date: dt.date, end_date: dt.date, client: senate_efd.SenateEFDClient | None = None) -> dict:
    client = client or senate_efd.SenateEFDClient()
    filings = client.fetch_filings(start_date, end_date)

    stats = {"filings_seen": len(filings), "filings_new": 0, "transactions_new": 0, "transactions_no_ticker": 0}
    for filing in filings:
        existing = session.query(Disclosure).filter_by(filing_id=filing.uuid).one_or_none()
        if existing:
            continue
        politician = _get_or_create_politician(session, filing.first_name, filing.last_name, "SENATE")
        disclosure = Disclosure(
            politician_id=politician.politician_id,
            chamber="SENATE",
            filing_id=filing.uuid,
            disclosure_date=filing.disclosure_date,
            disclosure_timestamp=filing.disclosure_timestamp,
            disclosure_confidence=filing.disclosure_confidence,
            source="senate_efd",
            source_url=filing.source_url,
        )
        session.add(disclosure)
        session.flush()
        stats["filings_new"] += 1

        for raw_txn in filing.transactions:
            ticker = normalize.clean_ticker(raw_txn.get("ticker"))
            base_asset_name, option_details = normalize.parse_senate_option_description(raw_txn.get("asset_name"))
            asset_type = normalize.map_asset_type(raw_txn.get("asset_type"))
            if option_details and not normalize.is_exercise_description(raw_txn.get("asset_name")):
                asset_type = AssetType.OPTION  # trust a successfully-parsed option description over the raw asset-type label
            elif option_details:
                option_details = None  # exercise event -> resolves to a stock position, not a fresh option (see normalize.is_exercise_description)
            txn_type = normalize.map_transaction_type(raw_txn.get("transaction_type"))
            owner = normalize.map_owner(raw_txn.get("owner"))
            amount_min, amount_max = normalize.parse_amount_range(raw_txn.get("amount"))
            try:
                txn_date = dt.datetime.strptime(raw_txn["transaction_date"], "%m/%d/%Y").date()
            except (KeyError, ValueError):
                logger.warning("Skipping transaction with unparseable date in filing %s: %r", filing.uuid, raw_txn)
                continue

            security = None
            if ticker:
                # Security.asset_type always describes the UNDERLYING instrument
                # (the ticker here is always the underlying's symbol, never an
                # options-specific symbol -- verified live for both chambers):
                # an option transaction's own type/strike/expiration lives on
                # the Transaction row instead (see model docstring).
                underlying_type = AssetType.COMMON_STOCK if asset_type == AssetType.OPTION else asset_type
                security = _get_or_create_security(session, ticker, base_asset_name, underlying_type)
            else:
                stats["transactions_no_ticker"] += 1

            session.add(
                Transaction(
                    disclosure_id=disclosure.disclosure_id,
                    security_id=security.security_id if security else None,
                    ticker_raw=raw_txn.get("ticker"),
                    asset_type=asset_type.value,
                    transaction_type=txn_type.value,
                    owner_type=owner.value,
                    transaction_date=txn_date,
                    amount_min=amount_min,
                    amount_max=amount_max,
                    parse_confidence="HIGH",
                    option_type=option_details["option_type"] if option_details else None,
                    strike_price=option_details["strike_price"] if option_details else None,
                    expiration_date=option_details["expiration_date"] if option_details else None,
                )
            )
            stats["transactions_new"] += 1
    session.flush()
    return stats


def ingest_house_year(session, year: int, client: house_clerk.HouseClerkClient | None = None) -> dict:
    client = client or house_clerk.HouseClerkClient()
    filings = client.fetch_ptr_filings_for_year(year)

    stats = {
        "filings_seen": len(filings),
        "filings_new": 0,
        "filings_ocr_required": 0,
        "transactions_new": 0,
    }
    for filing in filings:
        existing = session.query(Disclosure).filter_by(filing_id=filing.doc_id).one_or_none()
        if existing:
            continue
        politician = _get_or_create_politician(session, filing.first_name, filing.last_name, "HOUSE")
        disclosure = Disclosure(
            politician_id=politician.politician_id,
            chamber="HOUSE",
            filing_id=filing.doc_id,
            disclosure_date=filing.disclosure_date,
            disclosure_timestamp=None,  # House publishes no time-of-day -- see FEASIBILITY.md #3
            disclosure_confidence="DATE_ONLY_ASSUMED",
            source="house_clerk",
            source_url=filing.source_url,
            raw_document_ref=filing.parse_confidence,
        )
        session.add(disclosure)
        session.flush()
        stats["filings_new"] += 1
        if filing.parse_confidence == "OCR_REQUIRED_NOT_IMPLEMENTED":
            stats["filings_ocr_required"] += 1
            continue

        for raw_txn in filing.transactions:
            ticker = normalize.clean_ticker(raw_txn.get("ticker"))
            option_details = normalize.parse_house_option_description(raw_txn.get("description"))
            asset_type = normalize.map_asset_type(raw_txn.get("asset_type"))
            if option_details and not normalize.is_exercise_description(raw_txn.get("description")):
                asset_type = AssetType.OPTION  # trust a successfully-parsed option description over the fragile [XX] tag match
            elif option_details:
                option_details = None  # exercise event -> resolves to a stock position, not a fresh option (see normalize.is_exercise_description)
            txn_type = normalize.map_transaction_type(raw_txn.get("transaction_type"))
            owner = normalize.map_owner(raw_txn.get("owner_code"))
            amount_min, amount_max = normalize.parse_amount_range(raw_txn.get("amount"))
            try:
                txn_date = dt.datetime.strptime(raw_txn["transaction_date"], "%m/%d/%Y").date()
            except (KeyError, ValueError):
                continue

            underlying_type = AssetType.COMMON_STOCK if asset_type == AssetType.OPTION else asset_type
            security = _get_or_create_security(session, ticker, raw_txn.get("asset_name"), underlying_type) if ticker else None
            session.add(
                Transaction(
                    disclosure_id=disclosure.disclosure_id,
                    security_id=security.security_id if security else None,
                    ticker_raw=raw_txn.get("ticker"),
                    asset_type=asset_type.value,
                    transaction_type=txn_type.value,
                    owner_type=owner.value,
                    transaction_date=txn_date,
                    amount_min=amount_min,
                    amount_max=amount_max,
                    parse_confidence="PARSED_HEURISTIC",
                    option_type=option_details["option_type"] if option_details else None,
                    strike_price=option_details["strike_price"] if option_details else None,
                    expiration_date=option_details["expiration_date"] if option_details else None,
                )
            )
            stats["transactions_new"] += 1
    session.flush()
    return stats
