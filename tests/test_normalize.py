from ctbacktest.config import AssetType, OwnerType, TransactionType
from ctbacktest.ingestion.normalize import clean_ticker, map_asset_type, map_owner, map_transaction_type, parse_amount_range


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
