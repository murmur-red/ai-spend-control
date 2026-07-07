#!/usr/bin/env python3
"""
Tests for the subscriptions dogfood path: a flat-subscriptions CSV → full spend pipeline.
Run: python test_subscriptions.py
"""
from __future__ import annotations

import sys

from config import SpendConfig
from subscriptions import load_subscriptions, dataset_from_subscriptions, _recent_months
from reconcile import reconcile
from metrics import rate_portfolio, portfolio_monthly_cost, spend_allocation, upcoming_renewals

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


def main() -> None:
    cfg = SpendConfig(today="2026-07-06")
    subs = [
        {"vendor": "Anthropic", "category": "AI API", "monthly_cost": "400", "currency": "USD",
         "renewal_date": "2026-12-31", "owner": "platform", "cost_center": "ai"},
        {"vendor": "Notion", "category": "SaaS", "monthly_cost": "20", "currency": "USD",
         "renewal_date": "2026-08-15", "owner": "cs", "cost_center": "ops"},
    ]
    ds = dataset_from_subscriptions(subs, cfg, months=3)

    check("recent months are 3, sorted", _recent_months("2026-07-06", 3) == ["2026-05", "2026-06", "2026-07"])
    check("a vendor per subscription", len(ds.vendors) == 2)
    check("a FLAT_FEE commitment per line", all(ln.components[0].scheme.value == "FLAT_FEE" for ln in ds.commitment_lines))

    rec = reconcile(ds, cfg)
    check("flat subs reconcile with no exceptions", rec["exceptions"] == [])

    rated = rate_portfolio(ds, cfg)
    monthly = dict(portfolio_monthly_cost(rated))
    check("monthly total = sum of monthly costs (420)", monthly.get("2026-07") == 420.0)

    by_vendor = {x["key"]: x["cost"] for x in spend_allocation(ds, rated, "vendor")}
    check("Anthropic = $400 × 3 months", by_vendor.get("Anthropic") == 1200)
    by_cc = {x["key"]: x["cost"] for x in spend_allocation(ds, rated, "cost_center")}
    check("allocation by cost center (ai)", by_cc.get("ai") == 1200)

    renewals = {r["vendor"]: r["days_to_renewal"] for r in upcoming_renewals(ds, cfg, within_days=120)}
    check("Notion renewal surfaces within 120d", "Notion" in renewals and renewals["Notion"] == 40)

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
