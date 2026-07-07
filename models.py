"""Data model for AI+SaaS spend control (buy-side).

Hierarchical, reconcilable schema (frozen architecture): Vendor → Contract → CommitmentLine →
RateComponent, with UsageFact / InvoiceLine / OutcomeFact / FxRate as period-grained facts. Usage is
linked to a commitment only after reconcile() (commitment_line_id is optional here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional


class Scheme(str, Enum):
    FLAT_FEE = "FLAT_FEE"
    PER_SEAT = "PER_SEAT"
    TIERED_USAGE = "TIERED_USAGE"
    PREPAID_DRAWDOWN = "PREPAID_DRAWDOWN"
    RESERVED_CAPACITY = "RESERVED_CAPACITY"
    OVERAGE = "OVERAGE"


UNIT_TYPES = {"TOKEN", "SEAT", "REQUEST", "API_CALL", "GB"}


@dataclass
class Vendor:
    id: str
    name: str
    category: str
    base_currency: str
    source_vendor_ref: str


@dataclass
class Contract:
    id: str
    vendor_id: str
    start_date: date
    end_date: date
    renewal_date: date
    currency: str


@dataclass
class FxRate:
    currency: str
    period: str            # YYYY-MM
    rate_to_base: float


@dataclass
class RateComponent:
    id: str
    line_id: str
    order: int
    scheme: Scheme
    params: dict[str, Any]  # scheme-specific; validated by the adapter


@dataclass
class CommitmentLine:
    id: str
    contract_id: str
    model_or_endpoint: str          # "" = vendor-wide
    effective_from: str             # YYYY-MM
    effective_to: str               # YYYY-MM
    components: list[RateComponent] = field(default_factory=list)


@dataclass
class UsageFact:
    id: str
    source: str
    source_vendor_ref: str
    vendor_id: str
    period: str                     # YYYY-MM
    model_or_endpoint: str
    unit_type: str
    units_consumed: float
    team_id: str
    cost_center: str
    project_id: str
    commitment_line_id: Optional[str] = None   # set by reconcile() (piece 2)


@dataclass
class InvoiceLine:
    id: str
    vendor_id: str
    period: str
    invoiced_cost: float
    currency: str
    commitment_line_id: Optional[str] = None


@dataclass
class OutcomeFact:
    source: str
    period: str
    scope_type: str                 # "team" | "project"
    scope_id: str
    kpi_name: str
    kpi_value: float


@dataclass
class Dataset:
    vendors: list[Vendor]
    contracts: list[Contract]
    fx_rates: list[FxRate]
    commitment_lines: list[CommitmentLine]
    usage_facts: list[UsageFact]
    invoice_lines: list[InvoiceLine]
    outcomes: list[OutcomeFact]
    meta: dict[str, Any]            # source, data_as_of, freshness

    # Convenience lookups (built by the adapter after referential validation).
    vendors_by_id: dict[str, Vendor] = field(default_factory=dict)
    vendors_by_ref: dict[str, Vendor] = field(default_factory=dict)
    lines_by_id: dict[str, CommitmentLine] = field(default_factory=dict)
