"""
Corporate action handling.

VERIFIED (not assumed) while building this module: Yahoo's chart endpoint
already returns split-adjusted open/high/low/close by default -- checked
against AAPL's real 4-for-1 split on 2020-08-31, where raw open/close show no
discontinuity across the split date. `adj_close` layers dividend adjustment
on top of that (slightly below raw close, growing with accumulated
dividends). Consequently:
  - Entry prices and TP/SL triggers use raw OHLC directly (bar.open/high/
    low/close) -- they are already split-consistent, so a 10-for-1 split
    does not appear as a fabricated 90% drawdown.
  - Total-return calculations for benchmarks that are meant to include
    dividend reinvestment (e.g. "buy and hold SPY") use `adj_close`.

What this module does NOT do:
  - Ticker-symbol changes and mergers are NOT auto-resolved. A `Security` can
    carry a `ticker_aliases` JSON map (old_ticker -> new_ticker, effective_date)
    that a user populates manually; unresolved symbol changes simply mean the
    older ticker's price series stops and is treated as no-data-available
    (EXCLUDED_NO_PRICE_DATA) rather than guessed.
  - Delisted-security backfill is not implemented. Yahoo Finance (and most
    free sources) generally do not serve historical prices for tickers that
    are no longer listed anywhere, which is a real survivorship-bias source
    documented in FEASIBILITY.md #8 and surfaced in every report's Limitations
    section, not silently absorbed into the results.
"""

from __future__ import annotations

from ctbacktest.market_data.base import Bar


def adjusted_price(bar: Bar) -> float:
    """The price to use for backtesting purposes: split/dividend-adjusted
    close when available, else raw close (flagged by the caller as lower
    confidence -- see backtest/engine.py)."""
    return bar.adj_close if bar.adj_close is not None else bar.close


def adjustment_ratio(bar: Bar) -> float:
    """Ratio applied to raw OHLC to bring them onto the adjusted-close scale,
    so intraday triggers (TP/SL) are evaluated consistently with the price the
    position was sized at."""
    if bar.adj_close is None or bar.close == 0:
        return 1.0
    return bar.adj_close / bar.close


_GENERIC_NAME_TOKENS = {
    "INC", "INC.", "CORP", "CORPORATION", "CO", "CO.", "COMPANY", "LTD", "LTD.", "PLC",
    "GROUP", "HOLDINGS", "HOLDING", "THE", "CLASS", "CL", "COMMON", "STOCK", "SHARES",
    "TRUST", "FUND", "ETF", "A", "B", "C",
}


def names_plausibly_match(expected_name: str | None, provider_name: str | None) -> bool:
    """Guards against ticker-symbol reuse: a delisted company's old ticker can
    be re-assigned to an entirely unrelated new listing, and a provider's
    history for that symbol can then silently return the WRONG company's
    prices for old dates (confirmed live during development: Yahoo's "PARA"
    history returned prices in the tens of thousands of dollars per share for
    January 2024 -- Paramount Global traded around $12-14 then; Yahoo's own
    `longName` for that data was a since-relisted micro-cap, not Paramount).

    This is a coarse token-overlap heuristic, not a certified identity check
    -- it exists to catch the obviously-wrong case, not to guarantee
    correctness. When either name is missing, we can't check, so we don't
    block (returns True) -- see FEASIBILITY.md for why ticker-change handling
    is manual/best-effort, not fully automated."""
    if not expected_name or not provider_name:
        return True

    def _tokens(name: str) -> set[str]:
        cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name.upper())
        return {tok for tok in cleaned.split() if tok not in _GENERIC_NAME_TOKENS and len(tok) > 1}

    expected_tokens = _tokens(expected_name)
    provider_tokens = _tokens(provider_name)
    if not expected_tokens or not provider_tokens:
        return True
    return len(expected_tokens & provider_tokens) > 0


def resolve_ticker(security_ticker_aliases: dict | None, ticker: str, as_of) -> str:
    """Look up a manually-maintained alias map for a ticker that changed
    symbols. Returns the input ticker unchanged if no alias applies -- this
    function never guesses a successor ticker on its own."""
    if not security_ticker_aliases:
        return ticker
    for alias, mapping in security_ticker_aliases.items():
        if alias.upper() == ticker.upper():
            effective = mapping.get("effective_date")
            if effective is None or str(as_of) >= effective:
                return mapping.get("new_ticker", ticker)
    return ticker
