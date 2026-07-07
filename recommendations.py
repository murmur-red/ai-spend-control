"""Cadence-driven recommendations for Product 2 (buy-side spend).

For each vendor: compute WoW / MoM / QoQ from its weekly cost series, then escalate on a cost SPIKE
(for a buyer, a cost INCREASE is the risk):
  • WoW spike ≥ wow_spike → URGENT: investigate now, review the spike with the owner (calendar link).
  • MoM spike ≥ mom_spike → ELEVATED: monthly review + renegotiation prep.
  • QoQ spike ≥ qoq_spike → STRATEGIC: quarterly renegotiation / commitment right-sizing.
  • else → OK.
Weekly cost lives in data/weekly_cost.csv (separate from the gated core pipeline).
"""
from __future__ import annotations

import csv
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from core.cadence import cadence

URGENCY_RANK = {"OK": 0, "STRATEGIC": 1, "ELEVATED": 2, "URGENT": 3}


def load_weekly_cost(data_dir: str) -> dict[str, tuple[str, Optional[list[float]]]]:
    path = Path(data_dir) / "weekly_cost.csv"
    if not path.exists():
        return {}
    out: dict[str, tuple[str, Optional[list[float]]]] = {}
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                series = [float(x) for x in (r.get("weekly_cost_series") or "").split(";") if x.strip() != ""]
            except ValueError:
                series = []
            out[r["vendor_id"].strip()] = (r.get("owner", "").strip() or "(unassigned)", series or None)
    return out


def _calendar_link(vendor: str, owner: str, why: str) -> str:
    text = urllib.parse.quote(f"Spend spike: {vendor}")
    details = urllib.parse.quote(f"Owner: {owner}\nWhy: {why}")
    return f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={text}&details={details}"


def recommend_vendor(vendor_id: str, vendor_name: str, owner: str,
                     weekly: Optional[list[float]], config) -> dict[str, Any]:
    c = cadence(weekly) if weekly else {"wow": None, "mom": None, "qoq": None, "yoy": None}

    def pct(x): return f"{x*100:+.1f}%" if x is not None else "n/a"

    urgency, rec, action_text, action_link = "OK", "Spend stable — monitor.", "", ""

    if c["wow"] is not None and c["wow"] >= config.wow_spike:
        urgency = "URGENT"
        rec = f"Cost spiked WoW {pct(c['wow'])}. Investigate the driver; review the spike with {owner}."
        action_text = f"Review spike with {owner}"
        action_link = _calendar_link(vendor_name, owner, f"WoW cost {pct(c['wow'])}")
    elif c["mom"] is not None and c["mom"] >= config.mom_spike:
        urgency = "ELEVATED"
        rec = f"Cost up MoM {pct(c['mom'])}. Monthly review — prep renegotiation / usage controls."
        action_text = f"Monthly review ({owner})"
    elif c["qoq"] is not None and c["qoq"] >= config.qoq_spike:
        urgency = "STRATEGIC"
        rec = f"Cost up QoQ {pct(c['qoq'])}. Strategic renegotiation / commitment right-sizing."
        action_text = "Quarterly renegotiation"

    if weekly is None:
        note = "No weekly cost feed — connect one to enable week-over-week spike detection."
        rec = note if (urgency == "OK" and not action_text) else f"{rec} ({note})"

    return {
        "vendor_id": vendor_id, "vendor": vendor_name, "owner": owner,
        "wow": c["wow"], "mom": c["mom"], "qoq": c["qoq"],
        "current_weekly_cost": round(weekly[-1]) if weekly else None,
        "urgency": urgency, "recommendation": rec,
        "action_text": action_text, "action_link": action_link,
    }


def recommendations(dataset, config) -> list[dict]:
    """Recommendation rows per vendor, most-urgent first (then biggest spike, then name)."""
    weekly = load_weekly_cost(config.data_dir)
    rows = []
    for v in dataset.vendors:
        owner, series = weekly.get(v.id, ("(unassigned)", None))
        rows.append(recommend_vendor(v.id, v.name, owner, series, config))
    # Within an urgency band: biggest spike first; no-data (None) sorts LAST.
    return sorted(rows, key=lambda r: (-URGENCY_RANK.get(r["urgency"], 0),
                                       -(r["wow"] if r["wow"] is not None else float("-inf")),
                                       r["vendor"]))
