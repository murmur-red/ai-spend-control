#!/usr/bin/env python3
"""Tests for the company-level overview (company.py). Deterministic, no network. Run: python test_company.py"""
from __future__ import annotations

import sys

import company as c

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


def main() -> None:
    al = c.synth_company()
    check("generates allocations", len(al) > 50)
    check("has org hierarchy", {a.department for a in al} == {"Engineering", "Sales", "Marketing", "Ops"})

    # spend_by rolls up and is sorted descending
    dept = c.spend_by(al, "department")
    check("spend_by sums to total", abs(sum(x["cost"] for x in dept) - sum(a.monthly_cost for a in al)) < 1e-6)
    check("spend_by sorted desc", all(dept[i]["cost"] >= dept[i + 1]["cost"] for i in range(len(dept) - 1)))

    # drains exclude idle spend (Mara holds an idle Salesforce seat → must NOT be a top drain)
    drains = {x["key"] for x in c.top_drains(al, "person", 8)}
    check("idle seat-holder not counted as a drain", "Mara" not in drains)

    # dead spend finds the idle seats and totals reclaimable $
    dead = c.dead_spend(al)
    persons = {(i["person"], i["tool"]) for i in dead["items"]}
    check("idle Salesforce seats flagged", ("Mara", "Salesforce") in persons and ("Quinn", "Salesforce") in persons)
    check("reclaimable equals sum of items", abs(dead["reclaimable_monthly"] - sum(i["reclaimable"] for i in dead["items"])) < 1e-6)
    check("dead-spend count matches items", dead["count"] == len(dead["items"]))

    # token analysis: Eve is the heavy drain, Dan holds an unused budget
    ta = c.token_analysis(al)
    check("token drain identifies Eve", any(d["person"] == "Eve" for d in ta["drains"]))
    check("token drain is above median", all(d["vs_median"] >= 3 for d in ta["drains"]))
    check("unused-budget flags Dan", any(u["person"] == "Dan" for u in ta["unused_budget"]))

    # actions: reclaim + token-drain review, sorted by $ impact, CRM cancel needs approval
    acts = c.company_actions(al)
    check("produces action items", len(acts) >= 3)
    check("actions sorted by amount desc", all(acts[i]["amount"] >= acts[i + 1]["amount"] for i in range(len(acts) - 1)))
    check("CRM cancellation requires approval", any(a["requires_approval"] and "Salesforce" in a["title"] for a in acts))
    check("a token-drain action names the person", any("token drain" in a["title"].lower() for a in acts))

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
