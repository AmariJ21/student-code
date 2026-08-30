"""
Corporate action handling.

DECISION (documented, not hidden -- see FEASIBILITY.md #10 / spec section 10):
the backtester trades on `adj_close`-derived returns wherever adj_close is
available, and reconstructs OHLC-consistent trigger prices by applying the
same split/dividend adjustment ratio to open/high/low/close for a given bar.
This means a stock's split history is already reflected in the price series
we backtest on, so a 10-for-1 split shows up as continuous price action, not
a fabricated 90% drawdown.

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
