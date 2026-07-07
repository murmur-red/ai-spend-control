#!/usr/bin/env python3
"""Tests for warehouse_ingest — the real 'connect your data' path. No network. Run: python test_warehouse_ingest.py"""
from __future__ import annotations

import sys

from config import SpendConfig
from warehouse_ingest import dataset_from_usage_rows, validate_rows
from reconcile import reconcile
from metrics import rate_portfolio, portfolio_monthly_cost, spend_allocation

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


def main() -> None:
    cfg = SpendConfig(today="2026-07-07")
    good = [
        {"vendor": "Anthropic", "period": "2026-05", "unit_type": "TOKEN", "units_consumed": "4200000",
         "cost": "336", "team_id": "ml"},
        {"vendor": "Anthropic", "period": "2026-06", "unit_type": "token", "units_consumed": "5100000",
         "cost": "408", "team_id": "ml"},
        {"vendor": "Snowflake", "period": "2026-06", "unit_type": "GB", "units_consumed": "1200",
         "cost": "240", "team_id": "data"},
    ]
    ds, rep = dataset_from_usage_rows(good, cfg)
    check("builds vendors", rep["vendors"] == 2)
    check("all rows matched", rep["matched"] == 3 and not rep["rejected"])
    check("unit_type is case-normalised (token→TOKEN)", ds.usage_facts[1].unit_type == "TOKEN")

    reconcile(ds, cfg)
    rated = rate_portfolio(ds, cfg)
    monthly = dict(portfolio_monthly_cost(rated))
    check("rated cost reproduces real spend (May 336)", abs(monthly.get("2026-05", 0) - 336) < 1.0)
    by_team = {x["key"]: x["cost"] for x in spend_allocation(ds, rated, "team_id")}
    check("spend attributes to teams", by_team.get("ml", 0) > 700 and by_team.get("data", 0) == 240)

    # ── rejections: bad rows are dropped WITH a reason, never faked ──
    bad = [
        {"vendor": "", "period": "2026-06", "unit_type": "TOKEN", "units_consumed": "1"},          # no vendor
        {"vendor": "X", "period": "2026/06", "unit_type": "TOKEN", "units_consumed": "1"},         # bad period
        {"vendor": "X", "period": "2026-06", "unit_type": "BANANAS", "units_consumed": "1"},       # bad unit
        {"vendor": "X", "period": "2026-06", "unit_type": "TOKEN", "units_consumed": "-5"},        # negative
        {"vendor": "X", "period": "2026-06", "unit_type": "TOKEN", "units_consumed": "abc"},       # non-numeric
    ]
    v, rej = validate_rows(bad)
    check("every malformed row rejected", len(v) == 0 and len(rej) == 5)
    reasons = " ".join(r["reason"] for r in rej)
    check("reasons are specific", all(w in reasons for w in ["vendor", "YYYY-MM", "unit_type", "non-negative"]))

    # ── security: injection-looking strings are inert DATA, not executed ──
    evil = [{"vendor": "Robert'); DROP TABLE usage;--", "period": "2026-06", "unit_type": "TOKEN",
             "units_consumed": "10", "cost": "5", "team_id": "<script>alert(1)</script>"}]
    dse, repe = dataset_from_usage_rows(evil, cfg)
    check("malicious vendor stored verbatim as a string (no exec)", dse.vendors[0].name.startswith("Robert'"))
    check("malicious team kept as inert data", dse.usage_facts[0].team_id == "<script>alert(1)</script>")

    # ── un-priced when no cost column: flagged, rate defaults to 1.0 (not faked) ──
    nocost = [{"vendor": "Mystery", "period": "2026-06", "unit_type": "REQUEST", "units_consumed": "100"}]
    _, repn = dataset_from_usage_rows(nocost, cfg)
    check("vendor with no cost flagged un-priced", repn["unpriced_vendors"] == ["Mystery"])

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
