"""
Benchmarks the strategy is never evaluated against in isolation (spec section
17). Each one reuses the same execution/portfolio machinery as the real
strategy so the comparison is apples-to-apples on costs, just different entry
rules or exit rules.
"""

from __future__ import annotations

import copy
import datetime as dt

import numpy as np

from ctbacktest.backtest.engine import BacktestEngine, SimulatedTrade, TradeCandidate
from ctbacktest.config import BacktestConfig
from ctbacktest.market_data.base import MarketDataProvider
from ctbacktest.market_data.resolution import best_available_series


def spy_buy_hold_benchmark(trades: list[SimulatedTrade], market_data: MarketDataProvider, ticker: str = "SPY") -> list[float]:
    """Benchmark 1: buy SPY at the same entry timestamp as each real trade,
    hold for that trade's actual holding period, using the same execution
    model. Returns a list of per-trade SPY returns (None entries skipped)."""
    out = []
    for t in trades:
        if t.excluded_reason is not None or t.entry_timestamp is None or t.exit_timestamp is None:
            continue
        series, _ = best_available_series(market_data, ticker, t.entry_timestamp, t.exit_timestamp + dt.timedelta(days=1))
        if not series.bars:
            continue
        entry_bar = min(series.bars, key=lambda b: abs((b.ts - t.entry_timestamp).total_seconds()))
        exit_bar = min(series.bars, key=lambda b: abs((b.ts - t.exit_timestamp).total_seconds()))
        if entry_bar.open <= 0:
            continue
        out.append((exit_bar.close - entry_bar.open) / entry_bar.open)
    return out


def transaction_date_benchmark(
    candidates: list[TradeCandidate], config: BacktestConfig, market_data: MarketDataProvider
) -> list[SimulatedTrade]:
    """Benchmark 2: buy at the transaction date instead of the disclosure
    date. This is DELIBERATELY a look-ahead scenario (a real trader could not
    have known about the transaction on the transaction date -- only Congress
    knew) -- it exists purely as an upper-bound counterfactual: 'how much of
    the return comes from information Congress had first, vs. the specific
    disclosure-timing edge the strategy actually trades on.'"""
    lookahead_candidates = [
        TradeCandidate(
            transaction_id=c.transaction_id,
            politician_id=c.politician_id,
            security_id=c.security_id,
            ticker=c.ticker,
            disclosure_date=c.transaction_date,
            disclosure_timestamp=None,
            disclosure_confidence="BENCHMARK_LOOKAHEAD_TRANSACTION_DATE",
            transaction_date=c.transaction_date,
            owner_type=c.owner_type,
        )
        for c in candidates
    ]
    engine = BacktestEngine(config, market_data)
    return engine.run(lookahead_candidates)


def disclosure_date_hold_benchmark(
    candidates: list[TradeCandidate], config: BacktestConfig, market_data: MarketDataProvider
) -> list[SimulatedTrade]:
    """Benchmark 3: buy at the same disclosure-based entry as the real
    strategy, but simply hold for max_hold_days and sell at the close --
    no take-profit/stop-loss. Isolates whether the TP/SL rule itself adds
    value over a naive buy-and-hold-after-disclosure."""
    hold_only_config = copy.deepcopy(config)
    hold_only_config.strategy.take_profit = 10.0  # effectively unreachable; bypasses the enumerated-grid validator since we mutate post-construction, not via the public config API
    hold_only_config.strategy.stop_loss = None
    engine = BacktestEngine(hold_only_config, market_data)
    return engine.run(candidates)


def randomized_entry_benchmark(
    candidates: list[TradeCandidate],
    config: BacktestConfig,
    market_data: MarketDataProvider,
    n_trials: int = 10,
    max_shift_days: int = 180,
    seed: int = 1234,
) -> list[float]:
    """Benchmark 4: shift each candidate's entry date by a random offset
    (preserving the same strategy/holding-period rules), preserving each
    trade's original ticker. Aggregating many trials' mean net returns gives
    an empirical null distribution: if the real strategy's mean return isn't
    distinguishable from this, the disclosure timing itself isn't adding
    value beyond generic exposure to whatever stocks Congress happens to
    trade (spec section 16.F)."""
    rng = np.random.default_rng(seed)
    trial_means = []
    for _ in range(n_trials):
        shifted = []
        for c in candidates:
            offset = int(rng.integers(-max_shift_days, max_shift_days + 1))
            if offset == 0:
                offset = 1
            shifted_date = c.disclosure_date + dt.timedelta(days=offset)
            shifted.append(
                TradeCandidate(
                    transaction_id=c.transaction_id,
                    politician_id=c.politician_id,
                    security_id=c.security_id,
                    ticker=c.ticker,
                    disclosure_date=shifted_date,
                    disclosure_timestamp=None,
                    disclosure_confidence="BENCHMARK_RANDOMIZED_ENTRY",
                    transaction_date=c.transaction_date,
                    owner_type=c.owner_type,
                )
            )
        engine = BacktestEngine(config, market_data)
        results = engine.run(shifted)
        rets = [t.net_return for t in results if t.excluded_reason is None and t.net_return is not None]
        if rets:
            trial_means.append(float(np.mean(rets)))
    return trial_means
