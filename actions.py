"""Turn findings into ready-to-fire actions — close the loop with minimal manual work.

Each metric/finding becomes a concrete Action (owner, channel, $ impact, approval gate). Low-risk
actions (alerts, tickets, calendar prep) auto-fire; money-moving actions (invoice disputes, budget
renegotiations) are queued for one-click approval. Delivery goes through pluggable injected sinks
(action_sinks.py), so this is testable without credentials and swaps to real systems in prod.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from metrics import (billing_variance, total_waste, budget_breach, upcoming_renewals)
from recommendations import recommendations

URGENCY_RANK = {"OK": 0, "STRATEGIC": 1, "ELEVATED": 2, "URGENT": 3}


@dataclass
class Action:
    id: str
    source: str                 # which finding produced it
    title: str
    detail: str
    owner: str
    urgency: str
    channel: str                # slack | ticket | email | calendar
    amount: float               # $ impact (absolute), 0 if n/a
    requires_approval: bool      # money-moving → human sign-off before it fires
    payload: dict = field(default_factory=dict)


def derive_actions(dataset, rated, config, annual_budget: float = 200_000,
                   kpi_name: str = "tickets_resolved", dispute_threshold: float = 1_000.0,
                   renewal_window_days: int = 120) -> list[Action]:
    """Map the current findings to actions. Deterministic and side-effect-free (delivery is separate)."""
    contracts_by_id = {c.id: c for c in dataset.contracts}
    vendor_name = {v.id: v.name for v in dataset.vendors}

    def line_vendor(line_id: str) -> str:
        line = dataset.lines_by_id.get(line_id)
        c = contracts_by_id.get(line.contract_id) if line else None
        return vendor_name.get(c.vendor_id, "?") if c else "?"

    out: list[Action] = []

    # 1. Billing variance → dispute (money-moving → approval)
    for x in billing_variance(dataset, rated, config):
        if abs(x["variance"]) >= dispute_threshold:
            v = line_vendor(x["line_id"])
            out.append(Action(f"dispute-{x['line_id']}-{x['period']}", "billing_variance",
                              f"Dispute {v} invoice ({x['period']})",
                              f"Invoiced ${x['invoiced']:,.0f} vs rated ${x['expected']:,.0f} "
                              f"(variance ${x['variance']:,.0f}). Request itemization / credit.",
                              owner="AP + vendor owner", urgency="ELEVATED", channel="ticket",
                              amount=abs(x["variance"]), requires_approval=True))

    # 2. Waste → reclaim / right-size (auto ticket)
    waste = total_waste(rated)
    if waste["total_waste"] > 0:
        out.append(Action("waste-reclaim", "waste", "Right-size commitments to reclaim waste",
                          f"${waste['total_waste']:,.0f} wasted (${waste['prepaid_expiry_waste']:,.0f} "
                          f"expiring prepaid + ${waste['reserved_unused_waste']:,.0f} unused reserved).",
                          owner="FinOps", urgency="ELEVATED", channel="ticket",
                          amount=waste["total_waste"], requires_approval=False))

    # 3. Budget breach → renegotiate / cut (approval)
    monthly = [(p, sum(r.expected_cost for lid, rs in rated.items() for r in rs if r.period == p))
               for p in sorted({r.period for rs in rated.values() for r in rs})]
    breach = budget_breach(monthly, annual_budget=annual_budget)
    if breach["breach"]:
        gap = breach["projected_horizon"] - annual_budget
        out.append(Action("budget-renegotiate", "budget",
                          f"Budget breach{' in ' + str(breach['breach_in_months']) + ' mo' if breach['breach_in_months'] else ''} — renegotiate or cut scope",
                          f"Run-rate ${breach['run_rate_monthly']:,.0f}/mo → projected ${breach['projected_horizon']:,.0f} "
                          f"vs ${annual_budget:,.0f} budget (over by ${gap:,.0f}).",
                          owner="FinOps lead", urgency="STRATEGIC", channel="email",
                          amount=max(0.0, gap), requires_approval=True))

    # 4. Cost spikes → investigate / review (auto; URGENT via calendar)
    for r in recommendations(dataset, config):
        if r["urgency"] in ("URGENT", "ELEVATED"):
            out.append(Action(f"spike-{r['vendor_id']}", "cost_spike",
                              f"Review cost spike: {r['vendor']}", r["recommendation"],
                              owner=r["owner"], urgency=r["urgency"],
                              channel="calendar" if r["urgency"] == "URGENT" else "slack",
                              amount=0.0, requires_approval=False,
                              payload={"link": r.get("action_link", "")}))

    # 5. Upcoming renewals → prep (auto calendar)
    for rn in upcoming_renewals(dataset, config, within_days=renewal_window_days):
        out.append(Action(f"renewal-{rn['contract']}", "renewal",
                          f"Prep renewal: {rn['vendor']} ({rn['days_to_renewal']}d)",
                          f"Contract {rn['contract']} renews {rn['renewal_date']}. Review usage + renegotiate terms.",
                          owner="Procurement", urgency="STRATEGIC", channel="calendar",
                          amount=0.0, requires_approval=False))

    # 6. Uncommitted / shadow spend → govern (auto ticket)
    from reconcile import reconcile
    exc = reconcile(dataset, config)["exceptions"]
    shadow = [e for e in exc if e["type"] == "UNCOMMITTED_USAGE"]
    if shadow:
        out.append(Action("govern-shadow", "shadow_spend", "Bring shadow spend under contract",
                          f"{len(shadow)} usage record(s) with no commitment. Assign to a contract or block.",
                          owner="FinOps", urgency="ELEVATED", channel="ticket", amount=0.0, requires_approval=False))

    return sorted(out, key=lambda a: (-URGENCY_RANK.get(a.urgency, 0), -a.amount, a.id))
