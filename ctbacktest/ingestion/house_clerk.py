"""
Official House Clerk scraper (disclosures-clerk.house.gov).

Verified live against the real site while building this module:
  - GET  /public_disc/financial-pdfs/<year>FD.zip   -> bulk index (XML+TSV) of
        every filing that year: Prefix/Last/First/Suffix/FilingType/StateDst/
        Year/FilingDate/DocID. FilingType == "P" is a Periodic Transaction
        Report. No login/agreement wall (unlike Senate eFD).
  - GET  /public_disc/ptr-pdfs/<year>/<DocID>.pdf   -> the PTR itself.

No time-of-day is published anywhere in this pipeline -- FilingDate is a bare
date. Unlike the Senate, there is no per-filing HTML page with a timestamp.
See FEASIBILITY.md #3 for the resulting chamber asymmetry.

PDF text extraction: recent (roughly current electronic-filing era) PTRs carry
an embedded, subsetted text layer and are parsed with pdfplumber. Older
paper-era filings are scanned images with no text layer; those are detected
(near-empty extraction) and recorded as parse_confidence =
"OCR_REQUIRED_NOT_IMPLEMENTED" with no fabricated transaction rows -- OCR is
explicitly out of scope for this project (see FEASIBILITY.md #8).
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import re
import time
import zipfile
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

BASE = "https://disclosures-clerk.house.gov"
INDEX_URL = f"{BASE}/public_disc/financial-pdfs/{{year}}FD.zip"
PDF_URL = f"{BASE}/public_disc/ptr-pdfs/{{year}}/{{doc_id}}.pdf"

PTR_FILING_TYPE = "P"

# Real House PTR text (verified live against Pelosi's 2025 options filing,
# not just the simpler Rep. Allen stock-sale example seen earlier) can
# interleave columns so severely that a single sequential "asset, ticker,
# tag, type, dates, amount" regex breaks: e.g. the raw flow for one real
# transaction was literally "...Common P 01/14/2025 01/14/2025 $250,001 -
# Stock (GOOGL) [OP] $500,000" -- the ticker landed AFTER the type/dates/
# amount it should logically follow, because pdfplumber's text-flow
# reconstruction followed the PDF's underlying multi-column layout, not
# reading order. A fixed left-to-right grammar cannot handle that reliably.
#
# So instead of one sequential match per transaction, three independent
# "anchors" are found across the whole page and paired by proximity:
#   1. a ticker in parentheses,
#   2. a (type, date, date, amount) block,
#   3. a "D<garbled>: <description>." comment (which is where option
#      strike/expiration/call-put actually live -- see normalize.py).
# This tolerates the anchors appearing in either order around each other.
_TICKER_ANCHOR_RE = re.compile(r"\(([A-Z]{1,6}(?:\.[A-Z])?)\)")
_TYPE_DATE_AMOUNT_RE = re.compile(
    r"(?P<txn_type>P|S\s*\(partial\)|S|E)\s+"
    r"(?P<txn_date>\d{2}/\d{2}/\d{4})\s+"
    r"\d{2}/\d{2}/\d{4}\s+"  # notification date -- not used; disclosure_date comes from the index's FilingDate instead
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+)"
)
_DESCRIPTION_RE = re.compile(r"D\W{0,4}:\s*(?P<desc>.+?\.)")
_OWNER_CODE_RE = re.compile(r"\b(SP|DC|JT)\b")
_ASSET_TYPE_TAG_RE = re.compile(r"\[([A-Z]{1,3})\]")

_ASSET_TYPE_CODE_MAP = {"ST": "Stock", "OP": "Option", "PS": "Non-Public Stock"}
_TXN_TYPE_CODE_MAP = {"P": "Purchase", "S": "Sale (Full)", "E": "Exchange"}

_MAX_ANCHOR_DISTANCE_CHARS = 250  # sanity bound so a ticker from a totally different transaction never gets paired


@dataclass
class HousePTRFiling:
    doc_id: str
    last_name: str
    first_name: str
    state_district: str
    disclosure_date: dt.date
    source_url: str
    parse_confidence: str  # "PARSED_HEURISTIC" or "OCR_REQUIRED_NOT_IMPLEMENTED" or "NO_TRANSACTIONS_FOUND"
    transactions: list[dict] = field(default_factory=list)


class HouseClerkClient:
    def __init__(self, request_delay_seconds: float = 0.5, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ctbacktest-research/0.1 (personal, non-commercial research)"})
        self.request_delay_seconds = request_delay_seconds
        self.timeout = timeout

    def fetch_year_index(self, year: int) -> list[dict]:
        url = INDEX_URL.format(year=year)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        rows: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
            import xml.etree.ElementTree as ET

            root = ET.fromstring(zf.read(xml_name))
            for member in root.findall("Member"):
                rows.append(
                    {
                        "last": (member.findtext("Last") or "").strip(),
                        "first": (member.findtext("First") or "").strip(),
                        "filing_type": (member.findtext("FilingType") or "").strip(),
                        "state_dst": (member.findtext("StateDst") or "").strip(),
                        "year": (member.findtext("Year") or "").strip(),
                        "filing_date": (member.findtext("FilingDate") or "").strip(),
                        "doc_id": (member.findtext("DocID") or "").strip(),
                    }
                )
        return rows

    def _extract_pdf_text(self, content: bytes) -> str:
        import pdfplumber

        text_parts = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)

    def _parse_transactions(self, text: str) -> list[dict]:
        # PDF glyph-width placeholder NUL bytes (observed live -- e.g. a "D:"
        # label extracted as "D\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00:")
        # would otherwise defeat the description regex's bounded gap; just
        # strip them outright rather than trying to bound an unpredictable count.
        cleaned = text.replace("\x00", "")
        normalized = " ".join(cleaned.split())

        # Tags are intentionally NOT stripped globally here (contrast with
        # older behavior): each transaction's [XX] asset-type tag is instead
        # paired to its own specific ticker occurrence below, because the
        # same ticker can legitimately appear multiple times in one filing
        # with DIFFERENT asset types (confirmed live: Pelosi's NVDA appeared
        # as both [ST] stock sales and a separate [OP] option purchase in the
        # same PTR) -- a single global ticker->type dict would silently
        # misattribute one of them.
        ticker_matches = list(_TICKER_ANCHOR_RE.finditer(normalized))
        tag_matches = list(_ASSET_TYPE_TAG_RE.finditer(normalized))
        type_matches = sorted(_TYPE_DATE_AMOUNT_RE.finditer(normalized), key=lambda m: m.start())
        desc_matches = list(_DESCRIPTION_RE.finditer(normalized))

        used_ticker_idx: set[int] = set()
        used_tag_idx: set[int] = set()
        used_desc_idx: set[int] = set()
        transactions = []

        def _nearest_unused(matches, used: set[int], anchor_pos: int, max_dist: int):
            best_idx, best_dist = None, None
            for idx, m in enumerate(matches):
                if idx in used:
                    continue
                dist = min(abs(m.start() - anchor_pos), abs(m.end() - anchor_pos))
                if dist <= max_dist and (best_dist is None or dist < best_dist):
                    best_dist, best_idx = dist, idx
            return best_idx

        for i, tm in enumerate(type_matches):
            segment_end = type_matches[i + 1].start() if i + 1 < len(type_matches) else len(normalized)

            ticker_idx = _nearest_unused(ticker_matches, used_ticker_idx, tm.start(), _MAX_ANCHOR_DISTANCE_CHARS)
            if ticker_idx is None:
                continue  # no ticker found nearby -- likely a non-equity asset (bond/fund); out of scope, see module docstring
            used_ticker_idx.add(ticker_idx)
            ticker_match = ticker_matches[ticker_idx]
            ticker = ticker_match.group(1).upper()

            tag_idx = _nearest_unused(tag_matches, used_tag_idx, ticker_match.start(), 80)
            asset_code = tag_matches[tag_idx].group(1).upper() if tag_idx is not None else None
            if tag_idx is not None:
                used_tag_idx.add(tag_idx)

            desc_text = None
            for k, dm in enumerate(desc_matches):
                if k in used_desc_idx:
                    continue
                if tm.end() <= dm.start() < segment_end:
                    desc_text = dm.group("desc")
                    used_desc_idx.add(k)
                    break

            segment_start_for_name = type_matches[i - 1].end() if i > 0 else 0
            name_window_end = min(ticker_match.start(), tm.start())
            asset_name_raw = normalized[max(segment_start_for_name, name_window_end - 120) : name_window_end]
            owner_match = _OWNER_CODE_RE.search(asset_name_raw[-40:])
            asset_name = _OWNER_CODE_RE.sub("", asset_name_raw).strip(" .")

            txn_type_raw = tm.group("txn_type")
            txn_code = txn_type_raw.upper().split()[0]
            is_partial = "partial" in txn_type_raw.lower()
            transaction_type = "Sale (Partial)" if (txn_code == "S" and is_partial) else _TXN_TYPE_CODE_MAP.get(txn_code, "Unknown")

            transactions.append(
                {
                    "owner_code": owner_match.group(1).upper() if owner_match else None,
                    "asset_name": asset_name or None,
                    "ticker": ticker,
                    "asset_type": _ASSET_TYPE_CODE_MAP.get(asset_code, "Other") if asset_code else "Other",
                    "transaction_type": transaction_type,
                    "transaction_date": tm.group("txn_date"),
                    "amount": tm.group("amount"),
                    "description": desc_text,
                }
            )
        return transactions

    def fetch_ptr_filing(self, index_row: dict) -> HousePTRFiling:
        year = index_row["year"]
        doc_id = index_row["doc_id"]
        url = PDF_URL.format(year=year, doc_id=doc_id)
        disclosure_date = dt.datetime.strptime(index_row["filing_date"], "%m/%d/%Y").date()

        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        time.sleep(self.request_delay_seconds)

        text = self._extract_pdf_text(resp.content)
        if len(text.strip()) < 50:
            return HousePTRFiling(
                doc_id=doc_id,
                last_name=index_row["last"],
                first_name=index_row["first"],
                state_district=index_row["state_dst"],
                disclosure_date=disclosure_date,
                source_url=url,
                parse_confidence="OCR_REQUIRED_NOT_IMPLEMENTED",
                transactions=[],
            )

        transactions = self._parse_transactions(text)
        confidence = "PARSED_HEURISTIC" if transactions else "NO_TRANSACTIONS_FOUND"
        return HousePTRFiling(
            doc_id=doc_id,
            last_name=index_row["last"],
            first_name=index_row["first"],
            state_district=index_row["state_dst"],
            disclosure_date=disclosure_date,
            source_url=url,
            parse_confidence=confidence,
            transactions=transactions,
        )

    def fetch_ptr_filings_for_year(self, year: int) -> list[HousePTRFiling]:
        index_rows = [r for r in self.fetch_year_index(year) if r["filing_type"] == PTR_FILING_TYPE]
        filings = []
        for row in index_rows:
            try:
                filings.append(self.fetch_ptr_filing(row))
            except Exception:
                logger.exception("Failed to fetch/parse House PTR doc_id=%s", row.get("doc_id"))
        return filings
