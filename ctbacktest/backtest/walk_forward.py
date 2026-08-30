"""
Train/validation/test splitting and walk-forward windows (spec section 19).
`optimize` (see cli/main.py) only ever searches the predefined grid from
config.py over train/validation data; the test split is touched exactly once,
for a final confirmatory run -- this module doesn't enforce that by itself
(Python can't stop a caller from cheating), but it's the only place split
boundaries are computed, so the CLI has no reason to bypass it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class DateSplit:
    label: str
    start: dt.date
    end: dt.date


def train_validation_test_split(
    data_start: dt.date, data_end: dt.date, train_frac: float = 0.6, validation_frac: float = 0.2
) -> list[DateSplit]:
    """Splits are computed from the actually-ingested data range, not
    hard-coded calendar years that might not match what was really loaded
    (see IMPLEMENTATION_PLAN.md)."""
    total_days = (data_end - data_start).days
    train_end = data_start + dt.timedelta(days=int(total_days * train_frac))
    val_end = train_end + dt.timedelta(days=int(total_days * validation_frac))
    return [
        DateSplit("train", data_start, train_end),
        DateSplit("validation", train_end + dt.timedelta(days=1), val_end),
        DateSplit("test", val_end + dt.timedelta(days=1), data_end),
    ]


def walk_forward_windows(
    data_start: dt.date, data_end: dt.date, train_days: int = 365, test_days: int = 90, step_days: int | None = None
) -> list[tuple[DateSplit, DateSplit]]:
    """Rolling (train_window, test_window) pairs. step_days defaults to
    test_days, i.e. non-overlapping test windows walking forward in time."""
    step_days = step_days or test_days
    windows = []
    cursor = data_start
    while True:
        train_end = cursor + dt.timedelta(days=train_days)
        test_end = train_end + dt.timedelta(days=test_days)
        if test_end > data_end:
            break
        windows.append(
            (
                DateSplit(f"walk_forward_train_{cursor.isoformat()}", cursor, train_end),
                DateSplit(f"walk_forward_test_{train_end.isoformat()}", train_end + dt.timedelta(days=1), test_end),
            )
        )
        cursor += dt.timedelta(days=step_days)
    return windows
