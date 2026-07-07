"""Configuration for AI+SaaS spend control. Named/tunable; today is explicit for reproducibility."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SpendConfig:
    today: str = "2026-06-30"          # explicit, not now() — reproducible / back-testable
    base_currency: str = "USD"         # tenant reporting currency
    max_data_age_days: int = 45        # freshness SLA
    allow_stale: bool = False          # stale data hard-fails unless explicitly allowed
    data_dir: str = "data"             # folder holding the CSVs + meta.json
    # Versioned, config-driven model alias map for reconcile() (e.g. gpt-4-0613 -> gpt-4).
    model_aliases: dict = field(default_factory=dict)
    advisor_model: str = "claude-sonnet-5"   # the only LLM in either product (piece 5)
    api_key: str = ""                        # bring-your-own advisor key; falls back to env if empty
    # Cadence alert thresholds — for the BUYER a cost SPIKE (increase) is the risk to escalate.
    wow_spike: float = 0.10                  # week-over-week cost increase → investigate immediately
    mom_spike: float = 0.05                  # month-over-month increase → monthly review
    qoq_spike: float = 0.05                  # quarter-over-quarter increase → strategic review
