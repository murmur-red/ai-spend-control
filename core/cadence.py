"""Cadence primitives — period-over-period change from a series at multiple horizons.

Domain-agnostic: takes a plain numeric series (oldest → newest) and returns fractional changes over
1 / 4 / 13 / 52-period windows (WoW / MoM / QoQ / YoY when the series is weekly). A horizon returns
None when there isn't enough history for it — never a spurious number.
"""
from __future__ import annotations

from typing import Optional


def window_change(series: list[float], w: int) -> Optional[float]:
    """Fractional change of the last `w` periods vs the prior `w` (needs ≥ 2w points)."""
    if w < 1 or len(series) < 2 * w:
        return None
    latest = sum(series[-w:])
    prior = sum(series[-2 * w:-w])
    return (latest - prior) / prior if prior else None


def cadence(weekly: list[float]) -> dict[str, Optional[float]]:
    """WoW / MoM / QoQ / YoY fractional change from a WEEKLY series (windows 1, 4, 13, 52 weeks)."""
    return {
        "wow": window_change(weekly, 1),
        "mom": window_change(weekly, 4),
        "qoq": window_change(weekly, 13),
        "yoy": window_change(weekly, 52),
    }
