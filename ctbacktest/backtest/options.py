"""
Black-Scholes-Merton pricing for the LEAPS/short-dated call and put options
that show up throughout congressional disclosures (see FEASIBILITY.md's
options section for why this exists at all: the standout individual
performers' outsized returns are driven mostly by leveraged option
positions, not plain stock, so a system that only simulates common
stock/ETF trades cannot test that specific, well-documented motif).

Known, documented approximations (this is a backtest, not a pricing
engine for real trading):
  - European-style BS is used for what are actually American-style equity
    options (can be exercised early). This is the standard simplification
    used in most option-strategy backtests when early-exercise modeling
    isn't feasible; it tends to slightly *understate* an early exercise's
    realized value for deep ITM calls near a dividend, which is a
    conservative-direction bias, not a favorable one.
  - Implied volatility is not available from any free historical source for
    the dates/strikes in question, so volatility is proxied by the
    underlying's own trailing realized (historical) volatility as of the
    entry date. Real IV usually runs above realized vol (a volatility risk
    premium), so this proxy will tend to UNDERPRICE the option somewhat,
    which -- for a strategy that BUYS options -- is again a conservative
    (not favorable) bias on entry cost, though it also affects the modeled
    exit price the same way, so its net effect on the position's *return*
    is smaller than it looks.
  - Risk-free rate and dividend yield are coarse constants/best-effort
    lookups (see market_data/security_metadata.py), not a real yield curve.

None of this is fabricated data -- every option price used in the engine is
computed from a real, documented model applied to real underlying prices
and a real trailing-volatility estimate, and every trade carries the
approximation as a visible fact (see BacktestTrade-level fields added for
option positions), not hidden inside an opaque "return".
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from enum import Enum

from scipy.stats import norm

from ctbacktest.market_data.base import Bar

MIN_TIME_TO_EXPIRY_YEARS = 1.0 / 365.0  # floor to avoid division-by-zero right at/after expiration
MIN_VOLATILITY = 0.05  # floor so a freakishly quiet trailing window doesn't produce a ~zero option price


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


@dataclass
class OptionSpec:
    option_type: OptionType
    strike: float
    expiration: dt.date


def year_fraction(start: dt.date, end: dt.date) -> float:
    return max((end - start).days, 0) / 365.0


def black_scholes_price(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    risk_free_rate: float,
    dividend_yield: float,
    option_type: OptionType,
) -> float:
    """Standard Black-Scholes-Merton price for a European option with
    continuous dividend yield. Returns intrinsic value once time-to-expiry
    hits the floor (i.e. at/after expiration) rather than blowing up."""
    t = max(time_to_expiry_years, 0.0)
    if t <= MIN_TIME_TO_EXPIRY_YEARS or spot <= 0 or strike <= 0:
        if option_type == OptionType.CALL:
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)

    vol = max(volatility, MIN_VOLATILITY)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * vol**2) * t) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    disc_div = math.exp(-dividend_yield * t)
    disc_rate = math.exp(-risk_free_rate * t)

    if option_type == OptionType.CALL:
        return spot * disc_div * norm.cdf(d1) - strike * disc_rate * norm.cdf(d2)
    return strike * disc_rate * norm.cdf(-d2) - spot * disc_div * norm.cdf(-d1)


def trailing_realized_volatility(bars: list[Bar], as_of_ts: dt.datetime, lookback_days: int = 90) -> float:
    """Annualized realized volatility of daily log returns over the trailing
    window ending at/before `as_of_ts`. See module docstring: this is our
    documented proxy for implied volatility, which we cannot obtain for free
    at historical dates/strikes."""
    window_start = as_of_ts - dt.timedelta(days=lookback_days * 2)  # generous calendar buffer for weekends/holidays
    closes = [b.close for b in bars if window_start <= b.ts <= as_of_ts and b.close > 0]
    closes = closes[-(lookback_days + 1):]
    if len(closes) < 5:
        return MIN_VOLATILITY
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(log_returns) < 4:
        return MIN_VOLATILITY
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(max(variance, 0.0))
    return max(daily_vol * math.sqrt(252), MIN_VOLATILITY)
