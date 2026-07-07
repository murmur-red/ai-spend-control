#!/usr/bin/env python3
"""
Tests for db_ingest.usage_from_rows — build validated UsageFacts from connector rows, rejecting bad
rows individually. Plus an end-to-end check that pulled warehouse usage flows through the pipeline.
Run: python test_db_ingest.py
"""
from __future__ import annotations

import sqlite3
import sys

from config import SpendConfig
from adapter import load_dataset
from reconcile import reconcile
from metrics import rate_portfolio, portfolio_monthly_cost
from connectors.sql import SQLConnector
from mapping import FieldMapping
from db_ingest import usage_from_rows

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


def main() -> None:
    # ── Row-level validation ────────────────────────────────────────────────
    rows = [
        {"id": "a", "vendor_id": "v1", "period": "2026-06", "unit_type": "TOKEN", "units_consumed": "1000", "model_or_endpoint": "gpt-4"},
        {"id": "b", "vendor_id": "", "period": "2026-06", "unit_type": "TOKEN", "units_consumed": "1"},       # no vendor
        {"id": "c", "vendor_id": "v1", "period": "2026-13", "unit_type": "TOKEN", "units_consumed": "1"},      # bad period
        {"id": "d", "vendor_id": "v1", "period": "2026-06", "unit_type": "WATTS", "units_consumed": "1"},      # bad unit
        {"id": "e", "vendor_id": "v1", "period": "2026-06", "unit_type": "SEAT", "units_consumed": "-5"},      # negative
    ]
    facts, rejected = usage_from_rows(rows)
    check("1 valid fact built", len(facts) == 1 and facts[0].vendor_id == "v1")
    check("units coerced to float", facts[0].units_consumed == 1000.0)
    check("4 bad rows rejected individually", len(rejected) == 4)
    check("rejections carry reasons", all("reason" in r for r in rejected))

    # ── End-to-end: pull warehouse usage → pipeline recomputes ─────────────
    cfg = SpendConfig()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE usage(usage_id TEXT,vend TEXT,meter_month TEXT,model TEXT,qty REAL,unit TEXT,team TEXT,project TEXT)")
    conn.executemany("INSERT INTO usage VALUES(?,?,?,?,?,?,?,?)", [
        ("w1", "v1", "2026-05", "gpt-4", 2_500_000, "TOKEN", "teamA", "projX"),
        ("w2", "v1", "2026-06", "gpt-4", 5_000_000, "TOKEN", "teamA", "projX"),
        ("w3", "v2", "2026-06", "", 85, "SEAT", "teamB", "projY"),
    ])
    conn.commit()
    ds = load_dataset(cfg)["dataset"]
    mapping = FieldMapping({"id": "usage_id", "vendor_id": "vend", "period": "meter_month",
                            "model_or_endpoint": "model", "units_consumed": "qty", "unit_type": "unit",
                            "team_id": "team", "project_id": "project"})
    mapped = mapping.apply(SQLConnector(conn, "SELECT * FROM usage").fetch(), constants={"source": "warehouse"})
    ds.usage_facts, _ = usage_from_rows(mapped)
    reconcile(ds, cfg)
    monthly = dict(portfolio_monthly_cost(rate_portfolio(ds, cfg)))
    check("warehouse usage reconciles + rates (June recomputed to $114,250)", monthly.get("2026-06") == 114250.0)

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
