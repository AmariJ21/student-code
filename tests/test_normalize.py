import datetime as dt

from ctbacktest.config import AssetType, OwnerType, TransactionType
from ctbacktest.ingestion.normalize import (
    clean_ticker,
    is_exercise_description,
    map_asset_type,
    map_owner,
    map_transaction_type,
    parse_amount_range,
    parse_house_option_description,
    parse_senate_option_description,
)


def test_parse_amount_range_typical():
    assert parse_amount_range("$50,001 - $100,000") == (50001.0, 100000.0)


def test_parse_amount_range_handles_embedded_newline_from_pdf_wrap():
    assert parse_amount_range("$50,001 -\n$100,000") == (50001.0, 100000.0)


def test_parse_amount_range_single_value():
    assert parse_amount_range("$15,000") == (15000.0, 15000.0)


def test_parse_amount_range_unparseable_returns_none_not_zero():
    assert parse_amount_range("N/A") == (None, None)
    assert parse_amount_range(None) == (None, None)


def test_map_owner_variants():
    assert map_owner("Spouse") == OwnerType.SPOUSE
    assert map_owner("JT") == OwnerType.JOINT
    assert map_owner("Dependent Child") == OwnerType.DEPENDENT
    assert map_owner("garbage") == OwnerType.UNKNOWN
    assert map_owner(None) == OwnerType.UNKNOWN


def test_map_transaction_type_never_defaults_to_buy():
    assert map_transaction_type("Purchase") == TransactionType.BUY
    assert map_transaction_type("Sale (Partial)") == TransactionType.SELL
    assert map_transaction_type("nonsense") == TransactionType.UNKNOWN


def test_map_asset_type_bonds_are_not_stock():
    assert map_asset_type("Municipal Security") == AssetType.BOND
    assert map_asset_type("Stock") == AssetType.COMMON_STOCK
    assert map_asset_type("Stock Option") == AssetType.OPTION
    assert map_asset_type("something weird") == AssetType.OTHER


def test_clean_ticker_rejects_placeholder_and_multiword_descriptions():
    assert clean_ticker("--") is None
    assert clean_ticker("AAPL") == "AAPL"
    assert clean_ticker(" msft ") == "MSFT"
    assert clean_ticker("JOHNSTON CNTY N C GO REF BDS 2010-A") is None


# --- Option parsing: verified live against real Senate/House filings during development ---


def test_parse_senate_option_description_real_format():
    base, opt = parse_senate_option_description("Ark Innovation ETF Option Type: Call Strike price: $51.00 Expires: 12/20/2024")
    assert base == "Ark Innovation ETF"
    assert opt == {"option_type": "CALL", "strike_price": 51.0, "expiration_date": dt.date(2024, 12, 20)}


def test_parse_senate_option_description_put():
    _, opt = parse_senate_option_description("Gilead Sciences Inc Option Type: Put Strike price: $70.00 Expires: 11/15/2024")
    assert opt["option_type"] == "PUT"


def test_parse_senate_option_description_plain_stock_returns_none():
    base, opt = parse_senate_option_description("Apple Inc. - Common Stock (AAPL)")
    assert opt is None
    assert base == "Apple Inc. - Common Stock (AAPL)"


def test_parse_house_option_description_purchase():
    opt = parse_house_option_description("Purchased 50 call options with a strike price of $80 and an expiration date of 1/16/26.")
    assert opt == {"option_type": "CALL", "strike_price": 80.0, "expiration_date": dt.date(2026, 1, 16)}


def test_parse_house_option_description_exercise_wording_variant():
    # Different connecting words ("at"/"with" instead of "with"/"and") -- verified live.
    opt = parse_house_option_description(
        "Exercised 500 call options purchased 11/22/23 (50,000 shares) at a strike price of $12 with an expiration date of 12/20/24."
    )
    assert opt == {"option_type": "CALL", "strike_price": 12.0, "expiration_date": dt.date(2024, 12, 20)}


def test_parse_house_option_description_plain_sale_returns_none():
    assert parse_house_option_description("Sold 31,600 shares.") is None
    assert parse_house_option_description(None) is None


def test_is_exercise_description():
    assert is_exercise_description("Exercised 500 call options...") is True
    assert is_exercise_description("Purchased 50 call options...") is False
    assert is_exercise_description(None) is False
