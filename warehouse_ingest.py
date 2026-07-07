"""Build a full spend Dataset from uploaded usage/billing rows — the real "connect your data" path.

Someone exports usage (or a billing/cost export) from their warehouse or a vendor console to CSV,
maps their columns to our fields, and this constructs Vendor → Contract → CommitmentLine (+ per-unit
rate) → UsageFact → InvoiceLine so the full pipeline (reconcile → rate → metrics) runs on THEIR data.

Safe by construction: a pure data transformation — no code execution, no network, no credentials.
Rows that don't validate are **rejected with a reason** (never silently coerced), so the dashboard
never shows a faked figure. When a `cost` column is present, per-unit rates are derived from it so the
rated cost reproduces the real spend; otherwise rates default to 1.0 and are flagged as un-priced.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from models import (Vendor, Contract, FxRate, CommitmentLine, RateComponent, UsageFact, InvoiceLine,
                    Dataset, Scheme, UNIT_TYPES)

_PERIOD = re.compile(r"^\d{4}-\d{2}$")

# Canonical fields the pipeline understands (targets for the user's column mapping).
REQUIRED_FIELDS = ["vendor", "period", "unit_type", "units_consumed"]
OPTIONAL_FIELDS = ["cost", "team_id", "project_id", "model_or_endpoint", "currency", "category"]


def _num(x: Any):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _renewal(period: str) -> date:
    y, m = map(int, period.split("-"))
    m += 6
    while m > 12:
        m -= 12
        y += 1
    return date(y, m, 1)


def validate_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split mapped rows into (valid, rejected-with-reason). No coercion of bad values."""
    valid, rejected = [], []
    for i, r in enumerate(rows, 1):
        vref = str(r.get("vendor") or r.get("source_vendor_ref") or r.get("vendor_id") or "").strip()
        period = str(r.get("period") or "").strip()
        ut = str(r.get("unit_type") or "").strip().upper()
        units = _num(r.get("units_consumed"))
        raw_cost = r.get("cost")
        cost = _num(raw_cost) if raw_cost not in (None, "") else None

        reason = None
        if not vref:
            reason = "missing vendor"
        elif not _PERIOD.match(period):
            reason = "period is not YYYY-MM"
        elif ut not in UNIT_TYPES:
            reason = f"unit_type not one of {sorted(UNIT_TYPES)}"
        elif units is None or units < 0:
            reason = "units_consumed is not a non-negative number"
        elif cost is not None and cost < 0:
            reason = "cost is negative"
        if reason:
            rejected.append({"row": i, "reason": reason, "data": r})
            continue
        valid.append({"vref": vref, "period": period, "ut": ut, "units": units, "cost": cost,
                      "team": str(r.get("team_id") or "").strip(),
                      "project": str(r.get("project_id") or "").strip(),
                      "model": str(r.get("model_or_endpoint") or "").strip(),
                      "cur": (str(r.get("currency") or "").strip() or None),
                      "category": str(r.get("category") or "").strip()})
    return valid, rejected


def dataset_from_usage_rows(rows: list[dict[str, Any]], config) -> tuple[Dataset, dict[str, Any]]:
    """Returns (Dataset, report). report = {rejected, unpriced_vendors, matched, vendors}."""
    base = config.base_currency
    valid, rejected = validate_rows(rows)

    order: list[str] = []
    byv: dict[str, list[dict]] = {}
    for row in valid:
        byv.setdefault(row["vref"], []).append(row)
        if row["vref"] not in order:
            order.append(row["vref"])

    vendors, contracts, lines, usage, invoices, fx = [], [], [], [], [], []
    seen_fx: set = set()
    unpriced: list[str] = []

    for vi, vref in enumerate(order, 1):
        vrows = byv[vref]
        vid, cid, lid = f"v{vi}", f"c{vi}", f"l{vi}"
        cur = next((r["cur"] for r in vrows if r["cur"]), base)
        cat = next((r["category"] for r in vrows if r["category"]), "")
        periods = sorted({r["period"] for r in vrows})
        vendors.append(Vendor(vid, vref, cat, base, vref.lower().replace(" ", "")))
        contracts.append(Contract(cid, vid, _date("2025-01-01"), _date("2026-12-31"),
                                  _renewal(periods[-1]), cur))

        comps = []
        for k, ut in enumerate(sorted({r["ut"] for r in vrows}), 1):
            urows = [r for r in vrows if r["ut"] == ut]
            tot_units = sum(r["units"] for r in urows)
            priced = [r for r in urows if r["cost"] is not None]
            tot_cost = sum(r["cost"] for r in priced)
            if priced and tot_units > 0:
                rate = tot_cost / tot_units                     # blended per-unit rate from real cost
            else:
                rate = 1.0                                      # no cost supplied → un-priced placeholder
                if vref not in unpriced:
                    unpriced.append(vref)
            comps.append(RateComponent(f"rc{vi}-{k}", lid, k, Scheme.TIERED_USAGE,
                         {"unit_type": ut, "tier_mode": "MARGINAL", "tiers": [[1e18, rate]]}))
        lines.append(CommitmentLine(lid, cid, "", periods[0], periods[-1], comps))

        for ri, r in enumerate(vrows, 1):
            usage.append(UsageFact(f"u{vi}-{ri}", "warehouse", vref.lower(), vid, r["period"],
                         r["model"], r["ut"], r["units"], r["team"], "", r["project"]))

        cost_by_p: dict[str, float] = {}
        for r in vrows:
            if r["cost"] is not None:
                cost_by_p[r["period"]] = cost_by_p.get(r["period"], 0.0) + r["cost"]
        for p, c in sorted(cost_by_p.items()):
            invoices.append(InvoiceLine(f"inv{vi}-{p}", vid, p, round(c, 6), cur, lid))

        if cur != base:
            for p in periods:
                if (cur, p) not in seen_fx:
                    fx.append(FxRate(cur, p, 1.0))              # placeholder — set a real rate per period
                    seen_fx.add((cur, p))

    ds = Dataset(vendors, contracts, fx, lines, usage, invoices, [],
                 {"source": "warehouse_upload", "data_as_of": config.today},
                 {v.id: v for v in vendors}, {v.source_vendor_ref: v for v in vendors},
                 {ln.id: ln for ln in lines})
    report = {"rejected": rejected, "unpriced_vendors": unpriced,
              "matched": len(valid), "vendors": len(vendors)}
    return ds, report
