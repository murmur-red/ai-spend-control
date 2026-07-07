#!/usr/bin/env python3
"""
Tests for Product 2 spend-cadence recommendations (WoW/MoM/QoQ cost-spike escalation).
Run: python test_recommendations.py
"""
from __future__ import annotations

import sys

from config import SpendConfig
from adapter import load_dataset
from recommendations import recommendations, recommend_vendor

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


def main() -> None:
    cfg = SpendConfig()
    ds = load_dataset(cfg)["dataset"]
    recs = {r["vendor_id"]: r for r in recommendations(ds, cfg)}

    # v1 LLM Cloud — WoW cost spike → URGENT + review-with-owner + calendar link
    v1 = recs["v1"]
    check("v1 urgency URGENT", v1["urgency"] == "URGENT")
    check("v1 WoW at/above spike", v1["wow"] is not None and v1["wow"] >= cfg.wow_spike)
    check("v1 action review-with-owner", v1["action_text"].startswith("Review spike with FinOps: Alex"))
    check("v1 calendar link", v1["action_link"].startswith("https://calendar.google.com"))

    # v2 SeatSuite — flat cost → OK
    check("v2 urgency OK", recs["v2"]["urgency"] == "OK")

    # Ordering: biggest spike first
    check("LLM Cloud sorted first", recommendations(ds, cfg)[0]["vendor"] == "LLM Cloud")

    # No weekly feed → guidance, cadence None
    nf = recommend_vendor("vX", "X Corp", "(unassigned)", None, cfg)
    check("no-feed wow None", nf["wow"] is None)
    check("no-feed guidance", "weekly" in nf["recommendation"].lower())

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
