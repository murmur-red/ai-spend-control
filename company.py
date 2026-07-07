"""Company-level spend & utilization — drill total → department → team → person.

Models per-(person, tool) ALLOCATION vs actual USAGE across an org, so you can find:
  • drains      — who/what spends the most (heavy token users, expensive seats),
  • dead spend  — seats/budgets paid for but not used ("paying, not using"),
  • gaps        — spend with no owner, or allocation with zero usage.
Deterministic synthetic company for the demo; the same functions run on real HR + tool-usage data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Alloc:
    department: str
    team: str
    person: str
    tool: str
    category: str
    unit_type: str        # SEAT | TOKEN
    allocated: float      # seats (1) or token budget
    used: float           # active seats (0/1) or tokens used
    monthly_cost: float   # $ this allocation costs (seat price, or usage cost)


# Per-seat monthly prices for seat-based tools.
SEAT_PRICE = {"Notion": 10, "GitHub": 4, "Figma": 15, "Salesforce": 150, "Slack": 8}


def synth_company() -> list[Alloc]:
    """A deterministic ~28-person company with deliberate waste + drain patterns."""
    people = {
        "Engineering": {"Platform": ["Ava", "Ben", "Cara", "Dan"], "ML": ["Eve", "Finn", "Gwen"],
                        "Web": ["Hugo", "Iris", "Jack"]},
        "Sales": {"AE": ["Kim", "Leo", "Mara", "Nate"], "SDR": ["Omar", "Priya", "Quinn"]},
        "Marketing": {"Growth": ["Rae", "Sam", "Tess"], "Content": ["Uma", "Vic"]},
        "Ops": {"Finance": ["Will", "Xena"], "People": ["Yara", "Zed"]},
    }
    a: list[Alloc] = []

    def add(dept, team, person, tool, cat, ut, alloc, used, cost):
        a.append(Alloc(dept, team, person, tool, cat, ut, alloc, used, cost))

    for dept, teams in people.items():
        for team, members in teams.items():
            for i, p in enumerate(members):
                # Everyone gets Notion + Slack seats; some never log in (idle → waste).
                add(dept, team, p, "Notion", "SaaS", "SEAT", 1, 0 if (hash(p) % 4 == 0) else 1, SEAT_PRICE["Notion"])
                add(dept, team, p, "Slack", "SaaS", "SEAT", 1, 1, SEAT_PRICE["Slack"])
                # Engineering: GitHub seats + Anthropic tokens (with drains).
                if dept == "Engineering":
                    add(dept, team, p, "GitHub", "Dev", "SEAT", 1, 0 if p in ("Cara",) else 1, SEAT_PRICE["GitHub"])
                    base = {"Eve": 12_000_000, "Finn": 900_000}.get(p, 1_500_000 + (hash(p) % 3) * 400_000)
                    used = 0 if p == "Dan" else base                 # Dan: allocated tokens, using ~none
                    add(dept, team, p, "Anthropic", "AI API", "TOKEN", 3_000_000, used, round(used / 1000 * 0.008, 2))
                # Sales: Salesforce seats — two AEs never use it (expensive idle).
                if dept == "Sales":
                    idle = p in ("Mara", "Quinn")
                    add(dept, team, p, "Salesforce", "CRM", "SEAT", 1, 0 if idle else 1, SEAT_PRICE["Salesforce"])
                # Marketing: Figma seats; two non-designers hold seats they don't use.
                if dept == "Marketing":
                    add(dept, team, p, "Figma", "Design", "SEAT", 1, 0 if p in ("Tess", "Vic") else 1, SEAT_PRICE["Figma"])
    return a


def utilization(al: Alloc) -> float:
    return (al.used / al.allocated) if al.allocated else 0.0


def spend_by(allocs: list[Alloc], level: str) -> list[dict[str, Any]]:
    """Total monthly cost grouped by 'department' | 'team' | 'person' | 'tool' | 'category'."""
    agg: dict[str, float] = {}
    for al in allocs:
        agg[getattr(al, level)] = agg.get(getattr(al, level), 0.0) + al.monthly_cost
    return sorted([{"key": k, "cost": round(v, 2)} for k, v in agg.items()], key=lambda x: -x["cost"])


def top_drains(allocs: list[Alloc], level: str = "person", n: int = 5, floor: float = 0.3) -> list[dict[str, Any]]:
    """Biggest spenders on things they actually USE — idle spend is waste (see dead_spend), not drain."""
    active = [al for al in allocs if utilization(al) >= floor]
    return spend_by(active, level)[:n]


def token_analysis(allocs: list[Alloc]) -> dict[str, Any]:
    """Per-person token consumption: find the heavy drains and the allocated-but-unused budgets."""
    tok = [al for al in allocs if al.unit_type == "TOKEN"]
    if not tok:
        return {"drains": [], "unused_budget": []}
    used = sorted(al.used for al in tok)
    median = used[len(used) // 2] or 1
    drains, unused = [], []
    for al in tok:
        row = {"person": al.person, "team": al.team, "tokens": al.used,
               "cost": al.monthly_cost, "vs_median": round(al.used / median, 1)}
        if al.used >= 3 * median:                       # burning far above the team norm
            drains.append(row)
        elif al.allocated > 0 and utilization(al) < 0.1:  # holds a budget, using ~nothing
            unused.append(row)
    drains.sort(key=lambda x: -x["cost"])
    return {"drains": drains, "unused_budget": unused, "median_tokens": median}


def dead_spend(allocs: list[Alloc], floor: float = 0.3) -> dict[str, Any]:
    """Seats/budgets paid for but not (sufficiently) used. Returns the reclaimable items + total $."""
    items = []
    for al in allocs:
        u = utilization(al)
        if al.unit_type == "SEAT" and u < floor:
            items.append({"person": al.person, "team": al.team, "tool": al.tool,
                          "utilization": round(u, 2), "reclaimable": round(al.monthly_cost, 2)})
        elif al.unit_type == "TOKEN" and al.allocated > 0 and u < floor and al.monthly_cost < 1:
            # a token budget allocated to someone using ~nothing (paying for access, not using)
            items.append({"person": al.person, "team": al.team, "tool": al.tool,
                          "utilization": round(u, 2), "reclaimable": round(SEAT_PRICE.get(al.tool, 0), 2)})
    items.sort(key=lambda x: -x["reclaimable"])
    return {"items": items, "reclaimable_monthly": round(sum(i["reclaimable"] for i in items), 2), "count": len(items)}


def gaps(allocs: list[Alloc]) -> list[dict[str, Any]]:
    """Allocations with no owner/team, or cost with zero usage (structural gaps)."""
    out = []
    for al in allocs:
        if not al.person or not al.team:
            out.append({"tool": al.tool, "issue": "unowned allocation", "cost": al.monthly_cost})
        elif al.monthly_cost > 0 and al.used == 0 and al.unit_type == "SEAT":
            out.append({"person": al.person, "tool": al.tool, "issue": "paid seat, zero usage", "cost": al.monthly_cost})
    return out


def company_actions(allocs: list[Alloc]) -> list[dict[str, Any]]:
    """Smart suggestions + action items from the findings (owner + channel + $ impact)."""
    acts = []
    dead = dead_spend(allocs)
    if dead["reclaimable_monthly"] > 0:
        acts.append({"title": f"Reclaim {dead['count']} idle seats — ${dead['reclaimable_monthly']:,.0f}/mo",
                     "detail": "Deprovision seats with no usage; reassign or downgrade the plan.",
                     "owner": "IT / FinOps", "channel": "ticket", "urgency": "ELEVATED",
                     "amount": dead["reclaimable_monthly"], "requires_approval": False})
    # AI-token drain → coach / optimize the specific heavy user
    ta = token_analysis(allocs)
    if ta["drains"]:
        d = ta["drains"][0]
        acts.append({"title": f"Review token drain: {d['person']} — {d['tokens']/1e6:.1f}M tokens "
                              f"(${d['cost']:,.0f}/mo, {d['vs_median']}× team median)",
                     "detail": "Confirm the workload is justified; optimize prompts / cache / right-size the model.",
                     "owner": f"{d['person']}'s manager ({d['team']})", "channel": "slack", "urgency": "ELEVATED",
                     "amount": d["cost"], "requires_approval": False})
    for u in ta["unused_budget"]:
        acts.append({"title": f"Reclaim {u['person']}'s unused AI budget ({u['team']})",
                     "detail": "Holds a token allocation but consumes ~none — reassign or remove.",
                     "owner": "FinOps", "channel": "ticket", "urgency": "MONITOR",
                     "amount": 0.0, "requires_approval": False})
    # Expensive idle CRM seats → hard-dollar reclaim (approval)
    crm_idle = [i for i in dead["items"] if i["tool"] == "Salesforce"]
    if crm_idle:
        amt = sum(i["reclaimable"] for i in crm_idle)
        acts.append({"title": f"Cancel {len(crm_idle)} unused Salesforce seats — ${amt:,.0f}/mo",
                     "detail": f"Seats held by {', '.join(i['person'] for i in crm_idle)} with zero logins.",
                     "owner": "RevOps", "channel": "email", "urgency": "STRATEGIC",
                     "amount": amt, "requires_approval": True})
    return sorted(acts, key=lambda x: -x["amount"])
