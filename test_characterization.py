#!/usr/bin/env python3
"""
Characterization + validation tests for the data model + adapter (piece 1). Pins the load on the
synthetic dataset and proves referential validation rejects bad rows. Run: python test_characterization.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from config import SpendConfig
from adapter import load_dataset

GOLDEN_COUNTS = {"vendors": 2, "contracts": 2, "fx_rates": 3, "commitment_lines": 2,
                 "rate_components": 4, "usage_facts": 3, "invoice_lines": 3, "outcomes": 2, "rejected": 0}


def main() -> None:
    passed = failed = 0

    def check(label, got, want):
        nonlocal passed, failed
        if got == want:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL  {label}: got {got!r}, want {want!r}")

    # ── Happy path ──────────────────────────────────────────────────────────
    res = load_dataset(SpendConfig())
    ds, rep = res["dataset"], res["report"]
    for k, want in GOLDEN_COUNTS.items():
        check(f"counts.{k}", rep["counts"][k], want)
    check("freshness.age_days", rep["freshness"]["age_days"], 10)
    check("freshness.stale", rep["freshness"]["stale"], False)
    check("l1.component_order", [(c.order, c.scheme.value) for c in ds.lines_by_id["l1"].components],
          [(1, "PREPAID_DRAWDOWN"), (2, "TIERED_USAGE"), (3, "OVERAGE")])
    check("vendor_ref_lookup", ds.vendors_by_ref["llmcloud"].id, "v1")
    check("usage_unset_commitment", all(u.commitment_line_id is None for u in ds.usage_facts), True)

    # ── Referential rejection (validation is real, not a no-op) ──────────────
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(Path("data"), tmp / "data")
    with (tmp / "data" / "vendors.csv").open("a") as f:
        f.write(",EmptyId Co,SaaS,USD,emptyref\n")                    # empty primary key
    with (tmp / "data" / "contracts.csv").open("a") as f:
        f.write("cBAD,v999,2026-01-01,2026-12-31,2026-12-31,USD\n")   # unknown vendor
    with (tmp / "data" / "usage_facts.csv").open("a") as f:
        f.write("uBAD,billing-api,llmcloud,v999,2026-06,gpt-4,TOKEN,1000,teamA,cc-eng,projX\n")  # unknown vendor
        f.write("uMIX,billing-api,seatsuite,v1,2026-06,gpt-4,TOKEN,1000,teamA,cc-eng,projX\n")   # ref≠vendor_id
        f.write("u1,billing-api,llmcloud,v1,2026-06,gpt-4,TOKEN,500,teamA,cc-eng,projX\n")         # duplicate id u1
    import json
    rc_path = tmp / "data" / "rate_components.json"
    rc = json.loads(rc_path.read_text())
    rc.append("not-an-object")   # malformed entry must be rejected, not crash the file
    rc_path.write_text(json.dumps(rc))
    bad = load_dataset(SpendConfig(data_dir=str(tmp / "data")))
    check("rejected_bad_rows", bad["report"]["counts"]["rejected"], 6)
    check("bad_contract_excluded", all(c.id != "cBAD" for c in bad["dataset"].contracts), True)
    check("bad_usage_excluded", all(u.id not in ("uBAD", "uMIX") for u in bad["dataset"].usage_facts), True)
    check("no_duplicate_usage_id", len([u for u in bad["dataset"].usage_facts if u.id == "u1"]), 1)
    check("no_empty_vendor_id", all(v.id != "" for v in bad["dataset"].vendors), True)
    check("good_components_survived", bad["report"]["counts"]["rate_components"], 4)  # file not aborted
    shutil.rmtree(tmp)

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
