#!/usr/bin/env python3
"""
Tests for the metrics layer (piece 4): billing variance, waste, budget-breach forecast, and
cost-per-outcome (with gating). Run: python test_metrics.py
"""
from __future__ import annotations

import sys

from config import SpendConfig
from adapter import load_dataset
from reconcile import reconcile
from metrics import (rate_portfolio, billing_variance, total_waste, portfolio_monthly_cost,
                     budget_breach, cost_per_outcome, mom_qoq_cost)

passed = failed = 0


def check(label, got, want, tol=1e-6):
    global passed, failed
    ok = (got == want) if not isinstance(want, float) else (got is not None and abs(got - want) <= tol)
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")


def main() -> None:
    cfg = SpendConfig()
    ds = load_dataset(cfg)["dataset"]
    reconcile(ds, cfg)
    rated = rate_portfolio(ds, cfg)

    # ── Portfolio monthly cost ──────────────────────────────────────────────
    monthly = portfolio_monthly_cost(rated)
    check("monthly_cost", monthly, [("2026-05", 0.0), ("2026-06", 94000.0)])

    # ── Billing variance (invoiced vs rated expected) ───────────────────────
    bv = {(x["line_id"], x["period"]): x for x in billing_variance(ds, rated, cfg)}
    check("l1 May variance (invoiced $50k vs prepaid-covered $0)", bv[("l1", "2026-05")]["variance"], 50000.0)
    check("l1 Jun variance (matches)", bv[("l1", "2026-06")]["variance"], 0.0)
    check("l2 Jun variance (matches)", bv[("l2", "2026-06")]["variance"], 0.0)

    # ── Waste (none in the base dataset) ────────────────────────────────────
    check("total_waste", total_waste(rated)["total_waste"], 0.0)

    # ── Budget breach forecast ──────────────────────────────────────────────
    breach = budget_breach(monthly, annual_budget=200000, months_ahead=6, trailing=3)
    check("run_rate (mean of [0, 94000])", breach["run_rate_monthly"], 47000.0)
    check("breach flagged", breach["breach"], True)
    check("breach in 3 months", breach["breach_in_months"], 3)
    check("no breach under big budget", budget_breach(monthly, annual_budget=1_000_000)["breach"], False)

    # ── Cost per outcome (attributed cost ÷ KPI; gated when absent) ─────────
    cpo = {(x["scope_id"], x["period"]): x for x in cost_per_outcome(ds, rated, "tickets_resolved", "project")}
    check("projX Jun cost/outcome ($90k ÷ 1200)", cpo[("projX", "2026-06")]["cost_per_outcome"], 75.0)
    check("projX May gated (no outcome)", cpo[("projX", "2026-05")]["cost_per_outcome"], None)
    check("projY gated (different KPI)", cpo[("projY", "2026-06")]["cost_per_outcome"], None)

    # ── MoM / QoQ on cost (zero-guarded) ────────────────────────────────────
    mq = mom_qoq_cost(monthly)
    check("mom guarded", mq["mom"], 0.0)
    check("qoq guarded", mq["qoq"], 0.0)

    # ── Visibility: spend allocation + renewals ─────────────────────────────
    from metrics import spend_allocation, commitment_utilization, upcoming_renewals
    by_vendor = {x["key"]: x["cost"] for x in spend_allocation(ds, rated, "vendor")}
    check("allocation by vendor (LLM Cloud)", by_vendor.get("LLM Cloud"), 90000)
    check("allocation by vendor (SeatSuite)", by_vendor.get("SeatSuite"), 4000)
    by_team = {x["key"]: x["cost"] for x in spend_allocation(ds, rated, "team_id")}
    check("allocation by team (teamA)", by_team.get("teamA"), 90000)
    check("renewals returns both contracts", len(upcoming_renewals(ds, cfg)), 2)
    check("commitment utilization per line", len(commitment_utilization(rated)), 2)

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
