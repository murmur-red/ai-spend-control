"""Numeric series primitives — operate on ordered series (oldest → newest) and scalars only."""
from __future__ import annotations


def mom_velocity(series: list[float]) -> float:
    """Average period-over-period fractional change across the whole series."""
    if len(series) < 2:
        return 0.0
    changes = [(cur - prev) / prev for prev, cur in zip(series, series[1:]) if prev]
    return sum(changes) / len(changes) if changes else 0.0


def latest_change(series: list[float]) -> float:
    """Fractional change of the most recent point vs the one before it."""
    if len(series) >= 2 and series[-2]:
        return (series[-1] - series[-2]) / series[-2]
    return 0.0


def qoq_change(series: list[float]) -> float:
    """Change of the latest half of the series vs the prior half (smooths period noise)."""
    n = len(series)
    if n < 2:
        return 0.0
    half = n // 2
    prior = sum(series[:half])
    latest = sum(series[-half:])
    return (latest - prior) / prior if prior else 0.0


def run_rate(latest_period_value: float, periods_per_year: int = 12) -> float:
    """Annualize the latest period's value."""
    return latest_period_value * periods_per_year


def variance(commitment: float, realized: float) -> float:
    """Signed gap of realized vs commitment (negative = realized below commitment)."""
    return realized - commitment
