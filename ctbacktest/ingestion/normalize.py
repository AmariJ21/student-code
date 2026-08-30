"""
Shared parsing/normalization helpers used by both the Senate eFD and House
Clerk ingestion modules, so BUY/SELL classification, owner codes, asset types,
and dollar-amount ranges are mapped identically regardless of chamber.

Nothing here fabricates data: any string that can't be confidently mapped
falls through to UNKNOWN/OTHER rather than guessing.
"""

from __future__ import annotations

import datetime as dt
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


# Senate eFD embeds option details directly in the asset-name cell, verified
# live against a real Senator's PTR (e.g. "Ark Innovation ETF Option Type:
# Call Strike price: $51.00 Expires: 12/20/2024"). Two-digit or four-digit
# years both appear in practice.
_SENATE_OPTION_RE = re.compile(
    r"Option Type:\s*(?P<opt_type>Call|Put)\s*"
    r"Strike price:\s*\$(?P<strike>[\d,]+\.?\d*)\s*"
    r"Expires:\s*(?P<expiration>\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)

# House PTRs describe options in a free-text "D: ..." comment attached to the
# transaction row, e.g. "Purchased 50 call options with a strike price of $80
# and an expiration date of 1/16/26." or, for an exercise, "Exercised 500
# call options purchased 11/22/23 (50,000 shares) at a strike price of $12
# with an expiration date of 12/20/24." -- verified live against a real
# Pelosi PTR. The connecting words ("with"/"at", "and"/"with") vary, so the
# regex tolerates arbitrary text between the anchored phrases rather than
# assuming one exact wording.
_HOUSE_OPTION_RE = re.compile(
    r"(?P<opt_type>call|put)\s+options?"
    r".*?strike price of \$(?P<strike>[\d,]+\.?\d*)"
    r".*?expiration date\s*(?:of)?\s*(?P<expiration>\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE | re.DOTALL,
)


def _parse_flexible_date(raw: str) -> Optional[dt.date]:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_senate_option_description(asset_name: str | None) -> tuple[Optional[str], Optional[dict]]:
    """Returns (base_asset_name_without_option_suffix, option_details_or_None).
    option_details = {"option_type": "CALL"/"PUT", "strike_price": float, "expiration_date": date}."""
    if not asset_name:
        return asset_name, None
    m = _SENATE_OPTION_RE.search(asset_name)
    if not m:
        return asset_name, None
    expiration = _parse_flexible_date(m.group("expiration"))
    if expiration is None:
        return asset_name, None
    base_name = asset_name[: m.start()].strip()
    return base_name or asset_name, {
        "option_type": m.group("opt_type").upper(),
        "strike_price": float(m.group("strike").replace(",", "")),
        "expiration_date": expiration,
    }


_EXERCISE_RE = re.compile(r"\bexercised\b", re.IGNORECASE)


def is_exercise_description(text: str | None) -> bool:
    """An 'Exercised N call/put options...' disclosure resolves to holding the
    UNDERLYING stock, not a fresh option position -- the strike/expiration in
    its description refer to options bought potentially years earlier, not a
    new option we'd be simulating buying today (which would be economically
    nonsensical: buying an option at/after its own expiration date). See
    pipeline.py, which routes these to asset_type=COMMON_STOCK instead."""
    return bool(text) and bool(_EXERCISE_RE.search(text))


def parse_house_option_description(comment: str | None) -> Optional[dict]:
    """Returns option_details or None. `comment` is the House PTR's "D: ..."
    text associated with a [OP]-tagged transaction row."""
    if not comment:
        return None
    m = _HOUSE_OPTION_RE.search(comment)
    if not m:
        return None
    expiration = _parse_flexible_date(m.group("expiration"))
    if expiration is None:
        return None
    return {
        "option_type": m.group("opt_type").upper(),
        "strike_price": float(m.group("strike").replace(",", "")),
        "expiration_date": expiration,
    }


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
