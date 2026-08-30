"""
Politician metadata from the community-maintained unitedstates/congress-legislators
project (YAML, actively updated as of this project's research date -- see
FEASIBILITY.md #7/#9). This is used instead of the ProPublica Congress API,
which is confirmed dead (no new keys issued, docs kept only as historical
reference).

We pull both the "current" and "historical" files so politicians who left
office during the backtest window are still resolvable.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Iterator

import requests
import yaml

logger = logging.getLogger(__name__)

_BASE_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/{name}"
_FILES = ["legislators-current.yaml", "legislators-historical.yaml"]

_CHAMBER_MAP = {"rep": "HOUSE", "sen": "SENATE"}


@dataclass
class LegislatorRecord:
    bioguide_id: str
    full_name: str
    chamber: str
    party: str | None
    state: str | None
    district: str | None
    first_seen: dt.date | None
    last_seen: dt.date | None


def _fetch_yaml(name: str, timeout: int = 30) -> list[dict]:
    url = _BASE_URL.format(name=name)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return yaml.safe_load(resp.text)


def _parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _records_from_entry(entry: dict) -> Iterator[LegislatorRecord]:
    """
    One legislator can have multiple terms (chamber switches, redistricting,
    party changes). We emit one LegislatorRecord per (chamber) the person
    served in, using their most recent term details for that chamber, so a
    disclosure from any point in their career resolves to the right politician_id.
    """
    bioguide = entry.get("id", {}).get("bioguide")
    if not bioguide:
        return
    name = entry.get("name", {})
    full_name = name.get("official_full") or f"{name.get('first', '')} {name.get('last', '')}".strip()

    terms = entry.get("terms", [])
    by_chamber: dict[str, dict] = {}
    for term in terms:
        chamber = _CHAMBER_MAP.get(term.get("type"))
        if not chamber:
            continue
        # keep the latest term seen for this chamber
        prev = by_chamber.get(chamber)
        if prev is None or (term.get("start") or "") >= (prev.get("start") or ""):
            by_chamber[chamber] = term

    if not by_chamber:
        return

    all_starts = [_parse_date(t.get("start")) for t in terms if t.get("start")]
    all_ends = [_parse_date(t.get("end")) for t in terms if t.get("end")]
    first_seen = min([d for d in all_starts if d], default=None)
    last_seen = max([d for d in all_ends if d], default=None)

    for chamber, term in by_chamber.items():
        yield LegislatorRecord(
            bioguide_id=bioguide,
            full_name=full_name,
            chamber=chamber,
            party=term.get("party"),
            state=term.get("state"),
            district=str(term.get("district")) if term.get("district") is not None else None,
            first_seen=first_seen,
            last_seen=last_seen,
        )


def fetch_all_legislators() -> list[LegislatorRecord]:
    records: list[LegislatorRecord] = []
    for fname in _FILES:
        logger.info("Fetching %s", fname)
        entries = _fetch_yaml(fname)
        for entry in entries:
            records.extend(_records_from_entry(entry))
    return records


def upsert_legislators(session, records: list[LegislatorRecord] | None = None) -> int:
    """Insert-or-update Politician rows keyed by bioguide_id. Returns count upserted."""
    from ctbacktest.db.models import Politician

    if records is None:
        records = fetch_all_legislators()

    count = 0
    for rec in records:
        existing = (
            session.query(Politician)
            .filter_by(bioguide_id=rec.bioguide_id, chamber=rec.chamber)
            .one_or_none()
        )
        if existing is None:
            existing = Politician(bioguide_id=rec.bioguide_id, chamber=rec.chamber)
            session.add(existing)
        existing.full_name = rec.full_name
        existing.party = rec.party
        existing.state = rec.state
        existing.district = rec.district
        existing.first_seen = rec.first_seen
        existing.last_seen = rec.last_seen
        count += 1
    session.flush()
    return count
