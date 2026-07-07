#!/usr/bin/env python3
"""
Tests for the LLM advisor (piece 5). The LLM call is injected (mocked), so the context builder and
the grounding critic are tested deterministically — including rejection of a hallucinated figure.
An optional live smoke check runs only when RUN_LIVE_ADVISOR=1. Run: python test_advisor.py
"""
from __future__ import annotations

import os
import sys

from config import SpendConfig
from adapter import load_dataset
from reconcile import reconcile
from metrics import (rate_portfolio, billing_variance, total_waste, portfolio_monthly_cost,
                     budget_breach, cost_per_outcome)
from advisor import build_context, allowed_values, validate_findings, advise

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


def _context():
    cfg = SpendConfig()
    ds = load_dataset(cfg)["dataset"]
    reconcile(ds, cfg)
    rated = rate_portfolio(ds, cfg)
    monthly = portfolio_monthly_cost(rated)
    return build_context(billing_variance(ds, rated, cfg), total_waste(rated),
                         budget_breach(monthly, 200000), cost_per_outcome(ds, rated, "tickets_resolved"),
                         monthly), cfg


def main() -> None:
    ctx, cfg = _context()

    # ── allowed_values pulls the real figures ───────────────────────────────
    allowed = allowed_values(ctx)
    check("allowed has $50,000 billing variance", 50000.0 in allowed)
    check("allowed has $94,000 monthly", 94000.0 in allowed)
    check("allowed has $75 cost/outcome", 75.0 in allowed)
    check("allowed excludes a made-up number", 123456.0 not in allowed)

    # ── Critic: grounded finding kept, hallucinated finding rejected ────────
    def mock_llm(context, config):
        return [
            {"claim": "l1 was billed $50,000 for a prepaid-covered month", "cites_metric": "billing_variance",
             "cited_value": 50000.0, "recommended_action": "Reconcile the prepaid draw with the vendor",
             "est_savings": 50000.0},
            {"claim": "You can save $123,456 by switching tiers", "cites_metric": "invented",
             "cited_value": 123456.0, "recommended_action": "n/a", "est_savings": 123456.0},
        ]

    res = advise(ctx, cfg, llm=mock_llm)
    check("one grounded finding kept", len(res["valid"]) == 1)
    check("kept finding cites $50,000", res["valid"][0]["cited_value"] == 50000.0)
    check("one hallucinated finding rejected", len(res["rejected"]) == 1)
    check("rejection reason present", "reject_reason" in res["rejected"][0])

    # ── Empty findings → nothing kept, no crash ─────────────────────────────
    res0 = advise(ctx, cfg, llm=lambda c, cfg_: [])
    check("empty findings handled", res0["valid"] == [] and res0["rejected"] == [])

    # ── Critic unit: tolerance + non-numeric guard ──────────────────────────
    v = validate_findings([{"cited_value": True}, {"cited_value": "50000"}, {"cited_value": 75.0}], {75.0})
    check("bool/string cited_value rejected", len(v["valid"]) == 1 and v["valid"][0]["cited_value"] == 75.0)

    # ── Small-magnitude guardrail: 0.9 must NOT validate against a real 0.05 ─
    sm = validate_findings([{"cited_value": 0.9}, {"cited_value": 0.05}], {0.05})
    check("small-magnitude hallucination rejected", len(sm["valid"]) == 1 and sm["valid"][0]["cited_value"] == 0.05)

    # ── Exactness: 'close but not exact' large number rejected ──────────────
    ex = validate_findings([{"cited_value": 94009}, {"cited_value": 94000.0}], {94000.0})
    check("close-but-not-exact rejected", len(ex["valid"]) == 1 and ex["valid"][0]["cited_value"] == 94000.0)

    # ── Optional live smoke (only when explicitly enabled) ──────────────────
    if os.getenv("RUN_LIVE_ADVISOR") == "1" and os.getenv("ANTHROPIC_API_KEY"):
        live = advise(ctx, cfg)
        check("live returns validated structure", isinstance(live.get("valid"), list))

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
