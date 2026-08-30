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

# A PTR line item, once whitespace-normalized, looks roughly like:
#   "SP Albemarle Corporation (ALB) [ST] S 12/21/2023 01/08/2024 $1,001 - $15,000"
# Owner prefix (SP/DC/JT) is optional (blank = filer/self). The "[ST]" asset-type
# tag is dropped by _strip_asset_type_tags() before this regex runs, because
# pdfplumber's text-flow extraction was observed (on a real, live-fetched PTR)
# to sometimes relocate that tag to the middle of a wrapped dollar-amount range
# (e.g. "$50,001 - [ST] $100,000"), which would otherwise break amount parsing.
# The tag is captured separately by _extract_asset_type_codes() beforehand.
#
# NOTE: this only matches line items that have a ticker in parentheses, i.e.
# stocks/ETFs/options. Non-ticker assets (bonds, municipal securities, mutual
# funds without a symbol) are intentionally not extracted here -- the baseline
# strategy only acts on equity BUY signals, so those rows are out of scope
# rather than a parsing gap. This is a heuristic, best-effort parser; see
# FEASIBILITY.md #2/#8 for known limitations and why OCR-only filings are
# excluded outright.
_LINE_RE = re.compile(
    r"(?P<owner>SP|DC|JT)?\s*"
    r"(?P<asset>.+?)\s*"
    r"\((?P<ticker>[A-Z]{1,6}(?:\.[A-Z])?)\)\s*"
    r"(?P<txn_type>P|S(?:\s*\(partial\))?|E)\s+"
    r"(?P<txn_date>\d{2}/\d{2}/\d{4})\s+"
    r"\d{2}/\d{2}/\d{4}\s+"  # notification date -- not used; disclosure_date comes from the index's FilingDate instead
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+)",
    re.IGNORECASE,
)

_ASSET_TYPE_TAG_RE = re.compile(r"\[([A-Z]{1,3})\]")
_TICKER_WITH_TAG_RE = re.compile(r"\(([A-Z]{1,6}(?:\.[A-Z])?)\)\s*\[([A-Z]{1,3})\]")

_ASSET_TYPE_CODE_MAP = {"ST": "Stock", "OP": "Option", "PS": "Non-Public Stock"}
_TXN_TYPE_CODE_MAP = {"P": "Purchase", "S": "Sale (Full)", "E": "Exchange"}


def _extract_asset_type_codes(text: str) -> dict[str, str]:
    """Best-effort ticker -> asset-type-code map from the un-stripped text,
    for the common case where the [XX] tag immediately follows "(TICKER)"."""
    return {ticker.upper(): code.upper() for ticker, code in _TICKER_WITH_TAG_RE.findall(text)}


def _strip_asset_type_tags(text: str) -> str:
    return _ASSET_TYPE_TAG_RE.sub(" ", text)


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
        normalized = " ".join(text.split())
        ticker_to_asset_code = _extract_asset_type_codes(normalized)
        strippable = _strip_asset_type_tags(normalized)
        transactions = []
        for m in _LINE_RE.finditer(strippable):
            owner_code = (m.group("owner") or "").upper()
            ticker = m.group("ticker").upper()
            asset_code = ticker_to_asset_code.get(ticker)
            txn_type_raw = m.group("txn_type")
            txn_code = txn_type_raw.upper().split()[0]
            is_partial = "partial" in txn_type_raw.lower()
            transaction_type = "Sale (Partial)" if (txn_code == "S" and is_partial) else _TXN_TYPE_CODE_MAP.get(txn_code, "Unknown")
            transactions.append(
                {
                    "owner_code": owner_code or None,
                    "asset_name": m.group("asset").strip(),
                    "ticker": ticker,
                    "asset_type": _ASSET_TYPE_CODE_MAP.get(asset_code, "Other") if asset_code else "Other",
                    "transaction_type": transaction_type,
                    "transaction_date": m.group("txn_date"),
                    "amount": m.group("amount"),
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
