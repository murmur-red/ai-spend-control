"""rate() — the costing engine (frozen coverage-then-meter architecture).

Pure, deterministic. Per (line, period): FX-convert currency params, then
  Phase 1 (coverage, order-independent): FLAT_FEE / PER_SEAT fixed charges; RESERVED_CAPACITY covers
    reserved units; PREPAID_DRAWDOWN draws down carried balances FEFO (first-expiring-first-out).
  Phase 2 (meter): billable = usage − covered; TIERED_USAGE prices it (MARGINAL|VOLUME); OVERAGE
    bills the remainder beyond the tiers.
State (remaining prepaid per bucket) folds across periods, so rate() is a deterministic fold. Units
carry no currency; only rate params are converted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from models import Scheme


@dataclass
class RatedResult:
    line_id: str
    period: str
    expected_cost: float
    overage_units: float
    overage_cost: float
    reserved_utilization: dict[str, float]
    prepaid_burndown: dict[str, float]         # component_id -> remaining units
    prepaid_remaining_by_type: dict[str, float]  # unit_type -> remaining units (never mixed across types)
    prepaid_remaining_total: float             # coarse informational sum of bucket balances
    prepaid_runway_by_type: dict[str, Optional[float]]  # unit_type -> months of prepaid left
    prepaid_runway_months: Optional[float]     # soonest-depleting unit_type (the binding constraint)
    waste_cost: float                          # expiring unused prepaid (paid upfront, never consumed)
    reserved_waste_cost: float                 # reserved capacity paid for but not used this period
    covered_by_type: dict[str, float]
    billable_by_type: dict[str, float]


def price_tiers(units: float, tiers: list[list[float]], mode: str) -> tuple[float, float]:
    """Returns (cost, priced_units). tiers = [[up_to_cumulative, unit_price], ...] ascending,
    prices already FX-converted. MARGINAL: each band at its rate. VOLUME: all priced units at the
    rate of the band reached. Units beyond the top band are left unpriced (OVERAGE handles them)."""
    if units <= 0 or not tiers:
        return 0.0, 0.0
    if mode == "VOLUME":
        priced = min(units, tiers[-1][0])
        rate = tiers[-1][1]
        for up_to, price in tiers:
            if units <= up_to:
                rate = price
                break
        return priced * rate, priced
    # MARGINAL
    lower, cost, priced = 0.0, 0.0, 0.0
    for up_to, price in tiers:
        if units <= lower:
            break
        band = min(units, up_to) - lower
        if band > 0:
            cost += band * price
            priced += band
        lower = up_to
    return cost, priced


def rate(line, period: str, usage_by_type: dict[str, float], prior_state: dict[str, float],
         fx_rate: float, trailing_avg_by_type: dict[str, float]) -> tuple[RatedResult, dict[str, float]]:
    comps = line.components

    def conv(x: float) -> float:
        return x * fx_rate

    covered: dict[str, float] = {ut: 0.0 for ut in usage_by_type}
    cost = 0.0
    reserved_util: dict[str, float] = {}
    burndown: dict[str, float] = {}
    waste = 0.0
    reserved_waste = 0.0
    overage_units = overage_cost = 0.0
    next_state = dict(prior_state)

    def cover(ut: str, amt: float) -> None:
        covered[ut] = covered.get(ut, 0.0) + amt

    # ── Phase 1a: fixed charges (non-consuming) ─────────────────────────────
    for c in comps:
        if c.scheme is Scheme.FLAT_FEE:
            cost += conv(c.params["period_fee"])
            if c.params.get("unit_type"):
                cover(c.params["unit_type"], float(c.params.get("included_units", 0)))
        elif c.scheme is Scheme.PER_SEAT:
            ut = c.params["unit_type"]
            seats = float(c.params.get("seats", usage_by_type.get(ut, 0.0)))  # provisioned else active
            cost += seats * conv(c.params["unit_price"])
            cover(ut, seats)

    # ── Phase 1b: reserved capacity (pay for reserved regardless of use) ────
    for c in comps:
        if c.scheme is Scheme.RESERVED_CAPACITY:
            ut, ru = c.params["unit_type"], float(c.params["reserved_units"])
            reserved_cost = ru * conv(c.params["reserved_rate"])
            cost += reserved_cost
            cover(ut, ru)
            util = min(usage_by_type.get(ut, 0.0), ru) / ru if ru else 0.0
            reserved_util[c.id] = round(util, 6)
            reserved_waste += reserved_cost * (1.0 - util)   # paid-for reserved capacity left unused

    # ── Phase 1c: prepaid drawdown, FEFO across buckets (stateful) ──────────
    prepaids = [c for c in comps if c.scheme is Scheme.PREPAID_DRAWDOWN]
    prepaids.sort(key=lambda c: (c.params.get("expiry", "9999-99"),
                                 c.params.get("drawdown_priority", 10 ** 9), c.id))
    for c in prepaids:
        ut = c.params["unit_type"]
        bal = next_state.get(c.id, float(c.params["prepaid_units"]))
        draw = min(max(0.0, usage_by_type.get(ut, 0.0) - covered.get(ut, 0.0)), bal)
        cover(ut, draw)
        bal -= draw
        next_state[c.id] = bal
        burndown[c.id] = round(bal, 6)
        if c.params.get("expiry") == period and bal > 0:
            waste += bal * conv(c.params["unit_value"])

    # ── Phase 2: meter the billable remainder per unit_type ─────────────────
    tiered = {c.params["unit_type"]: c for c in comps if c.scheme is Scheme.TIERED_USAGE}
    overage = {c.params["unit_type"]: c for c in comps if c.scheme is Scheme.OVERAGE}
    billable: dict[str, float] = {}
    for ut in set(list(usage_by_type) + list(covered)):
        b = max(0.0, usage_by_type.get(ut, 0.0) - covered.get(ut, 0.0))
        billable[ut] = round(b, 6)
        priced = 0.0
        if ut in tiered:
            tiers = [[float(t[0]), conv(float(t[1]))] for t in tiered[ut].params["tiers"]]
            tc, priced = price_tiers(b, tiers, tiered[ut].params["tier_mode"])
            cost += tc
        if ut in overage:
            extra = max(0.0, b - priced)
            oc = extra * conv(overage[ut].params["overage_rate"])
            cost += oc
            overage_units += extra
            overage_cost += oc

    # Runway is per-unit-type (never sum tokens + seats + api_calls together). The scalar is the
    # soonest-depleting unit_type — the binding constraint.
    remaining_by_ut: dict[str, float] = {}
    for c in prepaids:
        remaining_by_ut[c.params["unit_type"]] = remaining_by_ut.get(c.params["unit_type"], 0.0) + next_state[c.id]
    runway_by_type: dict[str, Optional[float]] = {}
    for ut, rem in remaining_by_ut.items():
        a = trailing_avg_by_type.get(ut, 0.0)
        runway_by_type[ut] = round(rem / a, 4) if a > 0 else None
    defined = [v for v in runway_by_type.values() if v is not None]
    runway = min(defined) if defined else None

    res = RatedResult(
        line.id, period, round(cost, 6), round(overage_units, 6), round(overage_cost, 6),
        reserved_util, burndown, {k: round(v, 6) for k, v in remaining_by_ut.items()},
        round(sum(next_state.values()), 6), runway_by_type, runway, round(waste, 6),
        round(reserved_waste, 6), {k: round(v, 6) for k, v in covered.items()}, billable,
    )
    return res, next_state


def rate_line_over_periods(line, periods: list[str], usage_by_period: dict[str, dict[str, float]],
                           fx_by_period: dict[str, float], trailing_window: int = 3) -> list[RatedResult]:
    """Folds rate() across a line's periods (chronological), carrying prepaid state forward.
    trailing_avg for runway is computed from the prior `trailing_window` periods' usage."""
    # Track every unit_type that appears in any period (plus the line's prepaid unit_types), so a
    # zero-usage month is recorded as 0.0 in the trailing average — not silently skipped.
    tracked = {ut for p in periods for ut in usage_by_period.get(p, {})}
    tracked |= {c.params["unit_type"] for c in line.components if c.scheme is Scheme.PREPAID_DRAWDOWN}

    state: dict[str, float] = {}
    hist: dict[str, list[float]] = {ut: [] for ut in tracked}
    out: list[RatedResult] = []
    for period in periods:
        usage = usage_by_period.get(period, {})
        trailing = {}
        for ut in tracked:
            past = hist[ut][-trailing_window:]
            trailing[ut] = sum(past) / len(past) if past else 0.0
        res, state = rate(line, period, usage, state, fx_by_period.get(period, 1.0), trailing)
        out.append(res)
        for ut in tracked:
            hist[ut].append(usage.get(ut, 0.0))   # records 0.0 for zero-usage months
    return out
