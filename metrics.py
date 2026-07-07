"""Metrics — the payoff layer for buy-side spend control.

Consumes reconciled + rated data and produces the numbers a CFO/FinOps lead acts on:
- billing_variance: what the vendor invoiced vs. what our rate() model says it should cost.
- waste: expiring-unused prepaid + unused reserved capacity.
- budget_breach: forecast whether run-rate blows the annual budget, and when.
- cost_per_outcome: rated cost per business KPI (gated when the outcome is absent).
All money is normalized to the tenant base currency via FxRate(period).
"""
from __future__ import annotations

from typing import Any, Optional

from core import series
from rate import rate_line_over_periods


def _fx_lookup(dataset) -> dict[tuple[str, str], float]:
    return {(f.currency, f.period): f.rate_to_base for f in dataset.fx_rates}


def _to_base(amount: float, currency: str, period: str, base: str, fx: dict) -> Optional[float]:
    if currency == base:
        return amount
    rate = fx.get((currency, period))
    return amount * rate if rate is not None else None


def rate_portfolio(dataset, config) -> dict[str, list]:
    """Folds rate() over every reconciled line. Returns {line_id: [RatedResult, ...]} (chronological).
    Assumes reconcile() has run (usage.commitment_line_id set); unmatched usage is skipped."""
    contracts_by_id = {c.id: c for c in dataset.contracts}
    fx = _fx_lookup(dataset)

    per_line: dict[str, dict[str, dict[str, float]]] = {}
    for u in dataset.usage_facts:
        if u.commitment_line_id is None:
            continue
        bucket = per_line.setdefault(u.commitment_line_id, {}).setdefault(u.period, {})
        bucket[u.unit_type] = bucket.get(u.unit_type, 0.0) + u.units_consumed

    results: dict[str, list] = {}
    for line_id, ubp in per_line.items():
        line = dataset.lines_by_id[line_id]
        contract = contracts_by_id[line.contract_id]
        periods = sorted(ubp)
        fx_by_period = {p: (1.0 if contract.currency == config.base_currency
                            else fx.get((contract.currency, p), 1.0)) for p in periods}
        results[line_id] = rate_line_over_periods(line, periods, ubp, fx_by_period)
    return results


def billing_variance(dataset, rated: dict[str, list], config) -> list[dict[str, Any]]:
    """Per (line, period): invoiced (base) − expected (rated). Positive = billed more than modeled."""
    fx = _fx_lookup(dataset)
    expected = {(lid, r.period): r.expected_cost for lid, rs in rated.items() for r in rs}
    out = []
    for inv in dataset.invoice_lines:
        if inv.commitment_line_id is None:
            continue
        invoiced = _to_base(inv.invoiced_cost, inv.currency, inv.period, config.base_currency, fx)
        exp = expected.get((inv.commitment_line_id, inv.period))
        if invoiced is None or exp is None:
            continue
        variance = round(invoiced - exp, 6)
        out.append({"line_id": inv.commitment_line_id, "period": inv.period,
                    "invoiced": round(invoiced, 6), "expected": round(exp, 6), "variance": variance,
                    "variance_pct": round(variance / exp, 6) if exp else None})
    return sorted(out, key=lambda x: (x["line_id"], x["period"]))


def total_waste(rated: dict[str, list]) -> dict[str, float]:
    prepaid = sum(r.waste_cost for rs in rated.values() for r in rs)
    reserved = sum(r.reserved_waste_cost for rs in rated.values() for r in rs)
    return {"prepaid_expiry_waste": round(prepaid, 6),
            "reserved_unused_waste": round(reserved, 6),
            "total_waste": round(prepaid + reserved, 6)}


def portfolio_monthly_cost(rated: dict[str, list]) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for rs in rated.values():
        for r in rs:
            totals[r.period] = totals.get(r.period, 0.0) + r.expected_cost
    return [(p, round(totals[p], 6)) for p in sorted(totals)]


def budget_breach(monthly_cost: list[tuple[str, float]], annual_budget: float,
                  months_ahead: int = 6, trailing: int = 3) -> dict[str, Any]:
    """Projects forward at the trailing-N-month average run-rate; reports whether cumulative spend
    crosses the annual budget within the horizon, and in how many months."""
    costs = [c for _, c in monthly_cost]
    if not costs or annual_budget <= 0:
        return {"run_rate_monthly": 0.0, "cumulative_to_date": round(sum(costs), 6),
                "projected_horizon": round(sum(costs), 6), "breach": False, "breach_in_months": None}
    run_rate = sum(costs[-trailing:]) / len(costs[-trailing:])
    cum = sum(costs)
    breach_in = None
    for k in range(1, months_ahead + 1):
        if cum + run_rate * k > annual_budget:
            breach_in = k
            break
    projected = cum + run_rate * months_ahead
    return {"run_rate_monthly": round(run_rate, 6), "cumulative_to_date": round(cum, 6),
            "projected_horizon": round(projected, 6),
            "breach": projected > annual_budget or cum > annual_budget,
            "breach_in_months": breach_in}


def cost_per_outcome(dataset, rated: dict[str, list], kpi_name: str, scope_type: str = "project") -> list[dict[str, Any]]:
    """Attributes each line-period rated cost across its usage facts (by unit share) to scopes, then
    divides scope cost by the matching OutcomeFact KPI. Gated (None) when no outcome exists."""
    expected = {(lid, r.period): r.expected_cost for lid, rs in rated.items() for r in rs}
    scope_key = "project_id" if scope_type == "project" else "team_id"

    # Cost attributed to (scope_id, period) by each line-period's usage share.
    scope_cost: dict[tuple[str, str], float] = {}
    line_period_usage: dict[tuple[str, str], list] = {}
    for u in dataset.usage_facts:
        if u.commitment_line_id is None:
            continue
        line_period_usage.setdefault((u.commitment_line_id, u.period), []).append(u)
    for (lid, period), facts in line_period_usage.items():
        exp = expected.get((lid, period))
        if exp is None:
            continue
        total_units = sum(f.units_consumed for f in facts)
        for f in facts:
            share = (f.units_consumed / total_units) if total_units else (1.0 / len(facts))
            sk = (getattr(f, scope_key), period)
            scope_cost[sk] = scope_cost.get(sk, 0.0) + exp * share

    outcomes = {(o.scope_id, o.period): o.kpi_value for o in dataset.outcomes
                if o.scope_type == scope_type and o.kpi_name == kpi_name}
    out = []
    for (scope_id, period), cost in sorted(scope_cost.items()):
        kpi = outcomes.get((scope_id, period))
        out.append({"scope_type": scope_type, "scope_id": scope_id, "period": period,
                    "cost": round(cost, 6), "kpi_name": kpi_name, "kpi_value": kpi,
                    "cost_per_outcome": round(cost / kpi, 6) if kpi else None})   # gated when no/zero KPI
    return out


def mom_qoq_cost(monthly_cost: list[tuple[str, float]]) -> dict[str, float]:
    costs = [c for _, c in monthly_cost]
    return {"mom": round(series.latest_change(costs), 6), "qoq": round(series.qoq_change(costs), 6)}


# ── Visibility: spend allocation ("who's spending what") ───────────────────────
def spend_allocation(dataset, rated: dict[str, list], dim: str = "vendor") -> list[dict[str, Any]]:
    """Rated cost attributed by a dimension: 'vendor' | 'team_id' | 'cost_center' | 'project_id'.
    Attributes each line-period cost across its usage facts by unit share (same basis as cost-per-outcome)."""
    expected = {(lid, r.period): r.expected_cost for lid, rs in rated.items() for r in rs}
    vendor_name = {v.id: v.name for v in dataset.vendors}
    by_lp: dict[tuple[str, str], list] = {}
    for u in dataset.usage_facts:
        if u.commitment_line_id is not None:
            by_lp.setdefault((u.commitment_line_id, u.period), []).append(u)

    alloc: dict[str, float] = {}
    for (lid, period), facts in by_lp.items():
        exp = expected.get((lid, period))
        if exp is None:
            continue
        total = sum(f.units_consumed for f in facts)
        for f in facts:
            share = (f.units_consumed / total) if total else (1.0 / len(facts))
            key = vendor_name.get(f.vendor_id, f.vendor_id) if dim == "vendor" else (getattr(f, dim, "") or "(unattributed)")
            alloc[key] = alloc.get(key, 0.0) + exp * share
    return sorted([{"key": k, "cost": round(v)} for k, v in alloc.items()], key=lambda x: -x["cost"])


# ── Optimization: commitment utilization ──────────────────────────────────────
def commitment_utilization(rated: dict[str, list]) -> list[dict[str, Any]]:
    """Latest-period reserved utilization + prepaid remaining/runway per commitment line."""
    out = []
    for lid, rs in rated.items():
        if not rs:
            continue
        last = rs[-1]
        out.append({"line_id": lid, "period": last.period,
                    "reserved_utilization": last.reserved_utilization,
                    "prepaid_remaining": last.prepaid_remaining_total,
                    "prepaid_runway_months": last.prepaid_runway_months})
    return out


# ── Visibility: upcoming renewals (contract calendar) ─────────────────────────
def upcoming_renewals(dataset, config, within_days: int = 365) -> list[dict[str, Any]]:
    """Contracts renewing within `within_days` (plus any already overdue), soonest first."""
    from datetime import datetime
    today = datetime.strptime(config.today, "%Y-%m-%d").date()
    vendor_name = {v.id: v.name for v in dataset.vendors}
    out = []
    for c in dataset.contracts:
        days = (c.renewal_date - today).days
        if days <= within_days:
            out.append({"vendor": vendor_name.get(c.vendor_id, c.vendor_id), "contract": c.id,
                        "renewal_date": c.renewal_date.isoformat(), "days_to_renewal": days})
    return sorted(out, key=lambda x: x["days_to_renewal"])
