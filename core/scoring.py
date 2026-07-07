"""Scoring primitives — probability × magnitude, and score banding."""
from __future__ import annotations


def expected_value(probability: float, magnitude: float) -> float:
    """Probability-weighted magnitude (e.g. an expected loss)."""
    return probability * magnitude


def band(score: float, thresholds: list[tuple[float, str]], default: str) -> str:
    """Map a score to a label. `thresholds` = (min_score, label) pairs ordered highest-first;
    returns the first label whose min_score the score meets, else `default`."""
    for min_score, label in thresholds:
        if score >= min_score:
            return label
    return default
