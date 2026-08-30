"""
Official Senate eFD scraper (efdsearch.senate.gov).

Verified live against the real site while building this module:
  - GET  /search/home/                 -> csrftoken cookie + agreement form
  - POST /search/home/                 -> accept prohibition_agreement, sets session
  - POST /search/report/data/          -> DataTables JSON of matching filings
  - GET  /search/view/ptr/<uuid>/      -> HTML page with the transactions table
        and a "Filed MM/DD/YYYY @ H:MM AM/PM" line (real, minute-resolution
        disclosure timestamp -- see FEASIBILITY.md #3/#4).

Compliance: efdsearch.senate.gov's terms of use restrict use of Financial
Disclosure Report data (no commercial use, no credit-rating use, no
solicitation). This project is personal, non-commercial research. The scraper
refuses to run unless the user has explicitly set
CTBACKTEST_ACCEPT_SENATE_EFD_TERMS=true, mirroring the acknowledgment a human
would click through -- see .env.example and README.md.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE = "https://efdsearch.senate.gov"
HOME_URL = f"{BASE}/search/home/"
DATA_URL = f"{BASE}/search/report/data/"
VIEW_URL = f"{BASE}/search/view/ptr/{{uuid}}/"

PTR_REPORT_TYPE = 11
_UUID_RE = re.compile(r"/search/view/ptr/([0-9a-f-]+)/")
_FILED_RE = re.compile(r"Filed\s+(\d{2}/\d{2}/\d{4})\s*@\s*(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)


class SenateEFDTermsNotAccepted(RuntimeError):
    pass


@dataclass
class SenatePTRFiling:
    uuid: str
    first_name: str
    last_name: str
    office_display: str
    disclosure_date: dt.date
    disclosure_timestamp: dt.datetime | None  # tz-aware, assumed US/Eastern
    disclosure_confidence: str  # EXACT or DATE_ONLY_ASSUMED
    source_url: str
    transactions: list[dict] = field(default_factory=list)


def _require_terms_accepted() -> None:
    accepted = os.environ.get("CTBACKTEST_ACCEPT_SENATE_EFD_TERMS", "false").lower() == "true"
    if not accepted:
        raise SenateEFDTermsNotAccepted(
            "Senate eFD ingestion requires CTBACKTEST_ACCEPT_SENATE_EFD_TERMS=true "
            "in your environment. Read https://efdsearch.senate.gov/search/home/ 's "
            "terms of use first -- see FEASIBILITY.md #9 and README.md."
        )


class SenateEFDClient:
    def __init__(self, request_delay_seconds: float = 1.0, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ctbacktest-research/0.1 (personal, non-commercial research)"})
        self.request_delay_seconds = request_delay_seconds
        self.timeout = timeout
        self._agreed = False

    def _agree_to_terms(self) -> None:
        _require_terms_accepted()
        home = self.session.get(HOME_URL, timeout=self.timeout)
        home.raise_for_status()
        soup = BeautifulSoup(home.text, "lxml")
        token_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        if token_input is None:
            raise RuntimeError("Could not find csrfmiddlewaretoken on efdsearch home page; site markup may have changed.")
        csrf = token_input["value"]
        resp = self.session.post(
            HOME_URL,
            data={"csrfmiddlewaretoken": csrf, "prohibition_agreement": "1"},
            headers={"Referer": HOME_URL},
            timeout=self.timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
        self._agreed = True
        time.sleep(self.request_delay_seconds)

    def _ensure_ready(self) -> None:
        if not self._agreed:
            self._agree_to_terms()

    def search_ptr_filings(self, start_date: dt.date, end_date: dt.date, page_length: int = 100) -> list[dict]:
        """Returns raw DataTables rows: [first_name, last_name, office, link_html, filed_date_str]."""
        self._ensure_ready()
        csrf_token = self.session.cookies.get("csrftoken")
        rows: list[dict] = []
        start = 0
        while True:
            payload = {
                "draw": 1,
                "start": start,
                "length": page_length,
                "report_types": f"[{PTR_REPORT_TYPE}]",
                "filer_types": "[]",
                "submitted_start_date": start_date.strftime("%m/%d/%Y 00:00:00"),
                "submitted_end_date": end_date.strftime("%m/%d/%Y 23:59:59"),
                "candidate_state": "",
                "senator_state": "",
                "office_id": "",
                "first_name": "",
                "last_name": "",
            }
            resp = self.session.post(
                DATA_URL,
                data=payload,
                headers={"Referer": f"{BASE}/search/", "X-CSRFToken": csrf_token},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", [])
            if not data:
                break
            for row in data:
                first, last, office, link_html, filed_str = row
                m = _UUID_RE.search(link_html)
                if not m:
                    continue
                rows.append(
                    {
                        "uuid": m.group(1),
                        "first_name": first,
                        "last_name": last,
                        "office_display": office,
                        "filed_str": filed_str,
                    }
                )
            start += page_length
            if start >= body.get("recordsFiltered", 0):
                break
            time.sleep(self.request_delay_seconds)
        return rows

    def fetch_ptr_detail(self, uuid: str) -> tuple[dt.datetime | None, str, list[dict]]:
        """Returns (filed_timestamp_or_None, header_text, transaction_rows)."""
        self._ensure_ready()
        url = VIEW_URL.format(uuid=uuid)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        time.sleep(self.request_delay_seconds)
        soup = BeautifulSoup(resp.text, "lxml")

        filed_ts = None
        header_text = ""
        header_p = soup.find("p", class_="muted")
        if header_p:
            header_text = header_p.get_text(" ", strip=True)
            m = _FILED_RE.search(header_text)
            if m:
                date_part, time_part = m.group(1), m.group(2).upper().replace(" ", "")
                try:
                    naive = dt.datetime.strptime(f"{date_part} {time_part}", "%m/%d/%Y %I:%M%p")
                    filed_ts = naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=-5)))  # assumed US/Eastern (see FEASIBILITY.md #3)
                except ValueError:
                    logger.warning("Could not parse filed timestamp %r for %s", header_text, uuid)

        transactions = []
        table = soup.find("table", class_="table")
        if table and table.find("tbody"):
            for tr in table.find("tbody").find_all("tr", recursive=False):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 8:
                    continue
                # #, Transaction Date, Owner, Ticker, Asset Name, Asset Type, Type, Amount, Comment
                transactions.append(
                    {
                        "transaction_date": cells[1],
                        "owner": cells[2],
                        "ticker": cells[3],
                        "asset_name": cells[4],
                        "asset_type": cells[5],
                        "transaction_type": cells[6],
                        "amount": cells[7],
                    }
                )
        return filed_ts, header_text, transactions

    def fetch_filings(self, start_date: dt.date, end_date: dt.date) -> list[SenatePTRFiling]:
        results = []
        for row in self.search_ptr_filings(start_date, end_date):
            filed_ts, _, transactions = self.fetch_ptr_detail(row["uuid"])
            disclosure_date = dt.datetime.strptime(row["filed_str"], "%m/%d/%Y").date()
            results.append(
                SenatePTRFiling(
                    uuid=row["uuid"],
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    office_display=row["office_display"],
                    disclosure_date=disclosure_date,
                    disclosure_timestamp=filed_ts,
                    disclosure_confidence="EXACT" if filed_ts else "DATE_ONLY_ASSUMED",
                    source_url=VIEW_URL.format(uuid=row["uuid"]),
                    transactions=transactions,
                )
            )
        return results
