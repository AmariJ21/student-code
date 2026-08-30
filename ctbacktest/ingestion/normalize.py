"""
Shared parsing/normalization helpers used by both the Senate eFD and House
Clerk ingestion modules, so BUY/SELL classification, owner codes, asset types,
and dollar-amount ranges are mapped identically regardless of chamber.

Nothing here fabricates data: any string that can't be confidently mapped
falls through to UNKNOWN/OTHER rather than guessing.
"""

from __future__ import annotations

import re
from typing import Optional

from ctbacktest.config import AssetType, OwnerType, TransactionType

_AMOUNT_RE = re.compile(r"\$?([\d,]+)\s*-\s*\$?([\d,]+)")
_SINGLE_AMOUNT_RE = re.compile(r"\$?([\d,]+)")


def parse_amount_range(raw: str | None) -> tuple[Optional[float], Optional[float]]:
    """'$50,001 - $100,000' -> (50001.0, 100000.0). Unparsable -> (None, None)."""
    if not raw:
        return (None, None)
    text = " ".join(raw.split())  # collapse embedded newlines/whitespace
    m = _AMOUNT_RE.search(text)
    if m:
        lo = float(m.group(1).replace(",", ""))
        hi = float(m.group(2).replace(",", ""))
        return (lo, hi)
    m2 = _SINGLE_AMOUNT_RE.search(text)
    if m2:
        val = float(m2.group(1).replace(",", ""))
        return (val, val)
    return (None, None)


_OWNER_MAP = {
    "self": OwnerType.SELF,
    "sp": OwnerType.SPOUSE,
    "spouse": OwnerType.SPOUSE,
    "dc": OwnerType.DEPENDENT,
    "dependent": OwnerType.DEPENDENT,
    "dependent child": OwnerType.DEPENDENT,
    "jt": OwnerType.JOINT,
    "joint": OwnerType.JOINT,
}


def map_owner(raw: str | None) -> OwnerType:
    if not raw:
        return OwnerType.UNKNOWN
    return _OWNER_MAP.get(raw.strip().lower(), OwnerType.UNKNOWN)


_TXN_MAP = {
    "purchase": TransactionType.BUY,
    "buy": TransactionType.BUY,
    "p": TransactionType.BUY,
    "sale (full)": TransactionType.SELL,
    "sale (partial)": TransactionType.SELL,
    "sale": TransactionType.SELL,
    "s": TransactionType.SELL,
    "s (partial)": TransactionType.SELL,
    "exchange": TransactionType.EXCHANGE,
    "e": TransactionType.EXCHANGE,
}


def map_transaction_type(raw: str | None) -> TransactionType:
    if not raw:
        return TransactionType.UNKNOWN
    return _TXN_MAP.get(raw.strip().lower(), TransactionType.UNKNOWN)


# Senate eFD "Asset Type" values are a controlled free-text list; House PDFs
# use their own asset-type/category labels. Both funnel into the same enum.
_ASSET_TYPE_MAP = {
    "stock": AssetType.COMMON_STOCK,
    "common stock": AssetType.COMMON_STOCK,
    "stock option": AssetType.OPTION,
    "option": AssetType.OPTION,
    "etf": AssetType.ETF,
    "exchange traded fund": AssetType.ETF,
    "mutual fund": AssetType.MUTUAL_FUND,
    "corporate bond": AssetType.BOND,
    "municipal security": AssetType.BOND,
    "government bond": AssetType.BOND,
    "bond": AssetType.BOND,
    "non-public stock": AssetType.OTHER,
    "other securities": AssetType.OTHER,
    "other": AssetType.OTHER,
}


def map_asset_type(raw: str | None) -> AssetType:
    if not raw:
        return AssetType.OTHER
    key = raw.strip().lower()
    # Longest needle first: "stock option" must win over the shorter "stock"
    # substring it contains, or every option would be misclassified as a
    # plain equity purchase (see spec section 8: "do not blindly treat
    # options as ordinary stock purchases").
    for needle in sorted(_ASSET_TYPE_MAP, key=len, reverse=True):
        if needle in key:
            return _ASSET_TYPE_MAP[needle]
    return AssetType.OTHER


def clean_ticker(raw: str | None) -> Optional[str]:
    if not raw:
        return None
    t = " ".join(raw.split()).strip().upper()
    if t in {"", "--", "N/A", "NA", "NONE"}:
        return None
    # Options/bonds are sometimes given as "AAPL 01/17/25 200.00 C" style
    # descriptions rather than a bare ticker; take the leading token only
    # when it looks like a plain equity ticker (<=6 letters, optional class suffix).
    first_token = t.split()[0]
    if re.fullmatch(r"[A-Z]{1,6}(\.[A-Z])?", first_token):
        return first_token
    return None
