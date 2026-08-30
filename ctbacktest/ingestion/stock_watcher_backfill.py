"""
Optional supplemental backfill loader for the community-maintained Senate
Stock Watcher JSON dataset (github.com/timothycarambat/senate-stock-watcher-data).

This is NEVER the primary/authoritative source -- see FEASIBILITY.md #2:
House Stock Watcher's equivalent bucket was confirmed dead (HTTP 403, repo
stale since mid-2025) during this project's research pass, and even the
Senate Stock Watcher repo's live update cadence could not be verified as
current. Records loaded from here are tagged source="stock_watcher_backfill"
and disclosure_confidence="DATE_ONLY_ASSUMED", and ingest_disclosures()
(see cli/main.py) never lets a backfill row overwrite one that came from the
official senate_efd/house_clerk scrapers -- it only fills filing_id gaps.

Use case: bootstrapping older history quickly before running the (slower,
rate-limited) official scraper over the same range, or filling gaps if the
official site is temporarily unreachable.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

ALL_TRANSACTIONS_URL = (
    "https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/master/aggregate/all_transactions.json"
)


def fetch_backfill_transactions(timeout: int = 60) -> list[dict]:
    logger.warning(
        "Loading supplemental backfill data from a third-party community dataset "
        "(%s). This is NOT an official source and its update cadence is not "
        "guaranteed -- see FEASIBILITY.md #2. Records from this source are "
        "tagged accordingly and never override an official ingest.",
        ALL_TRANSACTIONS_URL,
    )
    resp = requests.get(ALL_TRANSACTIONS_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
