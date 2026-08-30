"""
Rolling, point-in-time politician leaderboard (spec pivot: research showed
performance is wildly dispersed across individuals -- only ~32% of disclosed
portfolios beat the S&P in 2025 -- so "follow Congress broadly" is the wrong
unit; "follow whoever has actually been performing" is what the standout
cases share).

The ONLY methodologically honest way to test that is causal: at the moment
we decide whether to admit a new candidate trade, rank politicians using
ONLY trades that have ALREADY CLOSED by that moment, over a trailing lookback
window. Never use a politician's future performance to decide whether to
follow their past disclosure -- that would be hindsight, not a backtest (see
the Pelosi/named-individual case-study mode in cli/main.py for where
hindsight-based selection is allowed, clearly labeled as such and never
used to claim a general edge).

Consequence worth stating up front, not discovering by surprise in a report:
early in any backtest window, most/all politicians have no trailing closed
trades yet (the backtest itself is the only source of track record), so the
leaderboard is naturally sparse/empty until enough trades have opened AND
closed. Run over a long enough window, or expect few admitted trades early.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class ClosedTradeRecord:
    politician_id: int
    exit_ts: dt.datetime
    net_return: float


@dataclass
class RankedPolitician:
    politician_id: int
    score: float
    n_trades: int


class PoliticianLeaderboard:
    def __init__(self, lookback_days: int = 365, min_track_record_trades: int = 3, top_k: int = 10):
        self.lookback_days = lookback_days
        self.min_track_record_trades = min_track_record_trades
        self.top_k = top_k
        self._history: dict[int, list[ClosedTradeRecord]] = defaultdict(list)

    def record_closed_trade(self, politician_id: int, exit_ts: dt.datetime, net_return: float) -> None:
        self._history[politician_id].append(ClosedTradeRecord(politician_id, exit_ts, net_return))

    def _trailing_trades(self, politician_id: int, as_of_ts: dt.datetime) -> list[ClosedTradeRecord]:
        cutoff = as_of_ts - dt.timedelta(days=self.lookback_days)
        # exit_ts < as_of_ts (strict): a trade closing at exactly this instant
        # is handled by the engine's settlement pass running before this call,
        # so by the time eligibility is checked it's already "in the past".
        return [r for r in self._history.get(politician_id, []) if cutoff <= r.exit_ts < as_of_ts]

    def ranked_politicians(self, as_of_ts: dt.datetime) -> list[RankedPolitician]:
        ranked = []
        for pid in self._history:
            trades = self._trailing_trades(pid, as_of_ts)
            if len(trades) >= self.min_track_record_trades:
                score = sum(t.net_return for t in trades) / len(trades)
                ranked.append(RankedPolitician(pid, score, len(trades)))
        return sorted(ranked, key=lambda r: r.score, reverse=True)

    def eligibility(self, politician_id: int, as_of_ts: dt.datetime) -> tuple[bool, str]:
        """Returns (eligible, reason_if_not). Never raises on an unseen
        politician -- they simply have no track record yet."""
        trades = self._trailing_trades(politician_id, as_of_ts)
        if len(trades) < self.min_track_record_trades:
            return False, "EXCLUDED_INSUFFICIENT_TRACK_RECORD"
        top_k_ids = {r.politician_id for r in self.ranked_politicians(as_of_ts)[: self.top_k]}
        if politician_id not in top_k_ids:
            return False, "EXCLUDED_NOT_IN_LEADERBOARD_TOP_K"
        return True, ""
