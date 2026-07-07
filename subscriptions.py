"""Build a spend Dataset from a flat-subscriptions CSV — dogfood your real SaaS with no admin keys.

Each subscription becomes a Vendor + Contract + CommitmentLine (FLAT_FEE = monthly cost) + an
InvoiceLine per recent month + a nominal UsageFact (so rate() runs). The full pipeline then produces
spend-by-vendor, monthly trend, renewals, budget, and cadence on YOUR numbers. Edit subscriptions.csv
with your real tools + costs.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from models import (Vendor, Contract, FxRate, CommitmentLine, RateComponent, UsageFact, InvoiceLine,
                    Dataset, Scheme, UNIT_TYPES)


def _recent_months(today: str, n: int) -> list[str]:
    d = datetime.strptime(today, "%Y-%m-%d")
    out = []
    for i in range(n):
        y, m = d.year, d.month - i
        while m <= 0:
            m += 12
            y -= 1
        out.append(f"{y:04d}-{m:02d}")
    return sorted(out)


def _date(s: str):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def load_subscriptions(csv_path: str) -> list[dict[str, str]]:
    with Path(csv_path).open(newline="") as f:
        return [row for row in csv.DictReader(f)]


def dataset_from_subscriptions(subs: list[dict[str, Any]], config, months: int = 3) -> Dataset:
    periods = _recent_months(config.today, months)
    base = config.base_currency
    vendors, contracts, lines, usage, invoices, fx = [], [], [], [], [], []
    seen_fx: set = set()

    for i, s in enumerate(subs, 1):
        name = s["vendor"].strip()
        cur = (s.get("currency") or base).strip() or base
        monthly = float(s["monthly_cost"])
        vid, cid, lid = f"v{i}", f"c{i}", f"l{i}"
        renewal = _date(s.get("renewal_date") or "2026-12-31")

        vendors.append(Vendor(vid, name, s.get("category", "").strip(), base, name.lower().replace(" ", "")))
        contracts.append(Contract(cid, vid, _date("2025-01-01"), _date("2026-12-31"), renewal, cur))
        lines.append(CommitmentLine(lid, cid, "", periods[0], periods[-1],
                     [RateComponent(f"rc{i}", lid, 1, Scheme.FLAT_FEE, {"period_fee": monthly})]))
        ut = (s.get("unit_type") or "REQUEST").strip().upper()
        if ut not in UNIT_TYPES:
            ut = "REQUEST"
        units = float(s.get("monthly_units") or 0)   # real usage from a console export; 0 = unknown
        for p in periods:
            usage.append(UsageFact(f"u{i}-{p}", "subscriptions", name.lower(), vid, p, "", ut, units,
                                   "", s.get("cost_center", "").strip(), s.get("owner", "").strip() or name))
            invoices.append(InvoiceLine(f"inv{i}-{p}", vid, p, monthly, cur, lid))
            if cur != base and (cur, p) not in seen_fx:
                fx.append(FxRate(cur, p, 1.0))   # placeholder — set a real rate for non-base currencies
                seen_fx.add((cur, p))

    return Dataset(vendors, contracts, fx, lines, usage, invoices, [],
                   {"source": "subscriptions", "data_as_of": config.today},
                   {v.id: v for v in vendors}, {v.source_vendor_ref: v for v in vendors},
                   {ln.id: ln for ln in lines})
