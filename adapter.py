"""Ingest gate for AI+SaaS spend control.

Loads meta.json + the relational CSVs into the typed model, validating schema, enums, per-scheme
RateComponent params, and referential integrity. Hard-fails on stale data (unless allowed); rejects
bad rows individually (one bad row never aborts a file); writes data/validation_report.json.
commitment_line_id on usage is left unset here — reconcile() (piece 2) assigns it.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from models import (Scheme, UNIT_TYPES, Vendor, Contract, FxRate, RateComponent,
                    CommitmentLine, UsageFact, InvoiceLine, OutcomeFact, Dataset)

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

REQUIRED = {
    "vendors.csv": {"id", "name", "category", "base_currency", "source_vendor_ref"},
    "contracts.csv": {"id", "vendor_id", "start_date", "end_date", "renewal_date", "currency"},
    "fx_rates.csv": {"currency", "period", "rate_to_base"},
    "commitment_lines.csv": {"id", "contract_id", "model_or_endpoint", "effective_from", "effective_to"},
    "usage_facts.csv": {"id", "source", "source_vendor_ref", "vendor_id", "period", "model_or_endpoint",
                        "unit_type", "units_consumed", "team_id", "cost_center", "project_id"},
    "invoice_lines.csv": {"id", "vendor_id", "period", "invoiced_cost", "currency", "commitment_line_id"},
    "outcome_facts.csv": {"source", "period", "scope_type", "scope_id", "kpi_name", "kpi_value"},
}

# Required params keys per scheme.
SCHEME_PARAMS = {
    Scheme.FLAT_FEE: {"period_fee"},
    Scheme.PER_SEAT: {"unit_type", "unit_price"},
    Scheme.TIERED_USAGE: {"unit_type", "tiers", "tier_mode"},
    Scheme.PREPAID_DRAWDOWN: {"unit_type", "prepaid_units", "unit_value"},
    Scheme.RESERVED_CAPACITY: {"unit_type", "reserved_units", "reserved_rate"},
    Scheme.OVERAGE: {"unit_type", "overage_rate"},
}


class SchemaError(RuntimeError):
    pass


class StaleDataError(RuntimeError):
    pass


def _read(dir_: Path, name: str) -> list[dict[str, str]]:
    path = dir_ / name
    if not path.exists():
        raise SchemaError(f"missing required file: {name}")
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED[name] - set(reader.fieldnames or [])
        if missing:
            raise SchemaError(f"{name}: missing columns {sorted(missing)}")
        # Strip every field uniformly on read so id/foreign-key lookups can't miss on whitespace.
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()} for row in reader]


def _period(s: str) -> str:
    if not PERIOD_RE.match(s.strip()):
        raise ValueError(f"bad period '{s}' (want YYYY-MM)")
    return s.strip()


def load_dataset(config) -> dict[str, Any]:
    d = Path(config.data_dir)
    meta_path = d / "meta.json"
    if not meta_path.exists():
        raise SchemaError("missing data/meta.json")
    meta = json.loads(meta_path.read_text())
    if "source" not in meta or "data_as_of" not in meta:
        raise SchemaError("meta.json needs 'source' and 'data_as_of'")
    age = (datetime.strptime(config.today, "%Y-%m-%d") - datetime.strptime(meta["data_as_of"], "%Y-%m-%d")).days
    stale = age > config.max_data_age_days
    if stale and not config.allow_stale:
        raise StaleDataError(f"data {age}d old > {config.max_data_age_days}d SLA; set allow_stale to override")

    rejected: list[dict[str, str]] = []

    def reject(name, key, reason):
        rejected.append({"file": name, "key": key, "reason": reason})

    # Primary/natural-key uniqueness trackers — a duplicate key is rejected, never silently overwritten.
    seen: dict[str, set] = {k: set() for k in
                            ("vendor_ref", "fx", "rc", "usage", "invoice", "outcome")}

    # ── Vendors (unique id + source_vendor_ref) ─────────────────────────────
    vendors, vendors_by_id, vendors_by_ref = [], {}, {}
    for r in _read(d, "vendors.csv"):
        try:
            vid, ref = r["id"], r["source_vendor_ref"].strip()
            if not vid:
                raise ValueError("empty vendor id")
            if not ref:
                raise ValueError("empty source_vendor_ref")
            if vid in vendors_by_id:
                raise ValueError(f"duplicate vendor id {vid}")
            if ref in seen["vendor_ref"]:
                raise ValueError(f"duplicate source_vendor_ref {ref}")
            seen["vendor_ref"].add(ref)
            v = Vendor(vid, r["name"], r["category"], r["base_currency"].strip(), ref)
            vendors.append(v); vendors_by_id[vid] = v; vendors_by_ref[ref] = v
        except (ValueError, KeyError) as e:
            reject("vendors.csv", r.get("id", "?"), str(e))

    # ── Contracts (ref: vendor) ─────────────────────────────────────────────
    contracts, contract_ids = [], set()
    for r in _read(d, "contracts.csv"):
        try:
            if not r["id"]:
                raise ValueError("empty contract id")
            if r["id"] in contract_ids:
                raise ValueError(f"duplicate contract id {r['id']}")
            if r["vendor_id"] not in vendors_by_id:
                raise ValueError(f"unknown vendor_id {r['vendor_id']}")
            c = Contract(r["id"], r["vendor_id"],
                         datetime.strptime(r["start_date"], "%Y-%m-%d").date(),
                         datetime.strptime(r["end_date"], "%Y-%m-%d").date(),
                         datetime.strptime(r["renewal_date"], "%Y-%m-%d").date(), r["currency"].strip())
            contracts.append(c); contract_ids.add(c.id)
        except (ValueError, KeyError) as e:
            reject("contracts.csv", r.get("id", "?"), str(e))

    # ── FX rates ────────────────────────────────────────────────────────────
    fx_rates = []
    for r in _read(d, "fx_rates.csv"):
        try:
            rate = float(r["rate_to_base"])
            if rate <= 0:
                raise ValueError("rate_to_base must be > 0")
            key = (r["currency"].strip(), _period(r["period"]))
            if not key[0]:
                raise ValueError("empty currency")
            if key in seen["fx"]:
                raise ValueError(f"duplicate fx rate for {key}")
            seen["fx"].add(key)
            fx_rates.append(FxRate(key[0], key[1], rate))
        except (ValueError, KeyError) as e:
            reject("fx_rates.csv", f"{r.get('currency')}/{r.get('period')}", str(e))

    # ── Commitment lines (ref: contract) ────────────────────────────────────
    lines, lines_by_id = [], {}
    for r in _read(d, "commitment_lines.csv"):
        try:
            if not r["id"]:
                raise ValueError("empty commitment line id")
            if r["id"] in lines_by_id:
                raise ValueError(f"duplicate commitment line id {r['id']}")
            if r["contract_id"] not in contract_ids:
                raise ValueError(f"unknown contract_id {r['contract_id']}")
            ln = CommitmentLine(r["id"], r["contract_id"], r["model_or_endpoint"].strip(),
                                _period(r["effective_from"]), _period(r["effective_to"]))
            lines.append(ln); lines_by_id[ln.id] = ln
        except (ValueError, KeyError) as e:
            reject("commitment_lines.csv", r.get("id", "?"), str(e))

    # ── Rate components (JSON; ref: line; per-scheme params) ────────────────
    rc_path = d / "rate_components.json"
    if not rc_path.exists():
        raise SchemaError("missing data/rate_components.json")
    for raw in json.loads(rc_path.read_text()):
        try:
            if not isinstance(raw, dict):
                raise ValueError("rate component entry is not a JSON object")
            r = {**raw, "id": str(raw.get("id", "")).strip(), "line_id": str(raw.get("line_id", "")).strip(),
                 "scheme": str(raw.get("scheme", "")).strip()}
            if not r["id"]:
                raise ValueError("empty rate component id")
            if r["id"] in seen["rc"]:
                raise ValueError(f"duplicate rate component id {r['id']}")
            seen["rc"].add(r["id"])
            if r["line_id"] not in lines_by_id:
                raise ValueError(f"unknown line_id {r['line_id']}")
            scheme = Scheme(r["scheme"])
            params = r["params"]
            missing = SCHEME_PARAMS[scheme] - set(params)
            if missing:
                raise ValueError(f"{scheme.value} missing params {sorted(missing)}")
            if "unit_type" in params and params["unit_type"] not in UNIT_TYPES:
                raise ValueError(f"bad unit_type {params['unit_type']}")
            if scheme is Scheme.TIERED_USAGE:
                if params["tier_mode"] not in ("MARGINAL", "VOLUME"):
                    raise ValueError("tier_mode must be MARGINAL|VOLUME")
                if not isinstance(params["tiers"], list) or not params["tiers"]:
                    raise ValueError("tiers must be a non-empty list")
            lines_by_id[r["line_id"]].components.append(
                RateComponent(r["id"], r["line_id"], int(r["order"]), scheme, params))
        except (ValueError, KeyError, TypeError) as e:
            reject("rate_components.json", (raw.get("id", "?") if isinstance(raw, dict) else "?"), str(e))
    for ln in lines:
        ln.components.sort(key=lambda c: c.order)

    # ── Usage facts (ref: vendor) ───────────────────────────────────────────
    usage = []
    for r in _read(d, "usage_facts.csv"):
        try:
            if not r["id"]:
                raise ValueError("empty usage fact id")
            if r["id"] in seen["usage"]:
                raise ValueError(f"duplicate usage fact id {r['id']}")
            seen["usage"].add(r["id"])
            if r["vendor_id"] not in vendors_by_id:
                raise ValueError(f"unknown vendor_id {r['vendor_id']}")
            if vendors_by_id[r["vendor_id"]].source_vendor_ref != r["source_vendor_ref"].strip():
                raise ValueError("source_vendor_ref inconsistent with vendor_id")
            if r["unit_type"].strip() not in UNIT_TYPES:
                raise ValueError(f"bad unit_type {r['unit_type']}")
            units = float(r["units_consumed"])
            if units < 0:
                raise ValueError("units_consumed < 0")
            usage.append(UsageFact(r["id"], r["source"].strip(), r["source_vendor_ref"].strip(),
                                   r["vendor_id"], _period(r["period"]), r["model_or_endpoint"].strip(),
                                   r["unit_type"].strip(), units, r["team_id"].strip(),
                                   r["cost_center"].strip(), r["project_id"].strip()))
        except (ValueError, KeyError) as e:
            reject("usage_facts.csv", r.get("id", "?"), str(e))

    # ── Invoice lines (ref: vendor; optional line) ──────────────────────────
    invoices = []
    for r in _read(d, "invoice_lines.csv"):
        try:
            if not r["id"]:
                raise ValueError("empty invoice line id")
            if r["id"] in seen["invoice"]:
                raise ValueError(f"duplicate invoice line id {r['id']}")
            seen["invoice"].add(r["id"])
            if r["vendor_id"] not in vendors_by_id:
                raise ValueError(f"unknown vendor_id {r['vendor_id']}")
            line_id = r["commitment_line_id"].strip() or None
            if line_id and line_id not in lines_by_id:
                raise ValueError(f"unknown commitment_line_id {line_id}")
            cost = float(r["invoiced_cost"])
            if cost < 0:
                raise ValueError("invoiced_cost < 0")
            invoices.append(InvoiceLine(r["id"], r["vendor_id"], _period(r["period"]), cost,
                                        r["currency"].strip(), line_id))
        except (ValueError, KeyError) as e:
            reject("invoice_lines.csv", r.get("id", "?"), str(e))

    # ── Outcomes ────────────────────────────────────────────────────────────
    outcomes = []
    for r in _read(d, "outcome_facts.csv"):
        try:
            if r["scope_type"].strip() not in ("team", "project"):
                raise ValueError("scope_type must be team|project")
            okey = (_period(r["period"]), r["scope_type"].strip(), r["scope_id"].strip(), r["kpi_name"].strip())
            if not okey[2] or not okey[3]:
                raise ValueError("empty outcome scope_id or kpi_name")
            if okey in seen["outcome"]:
                raise ValueError(f"duplicate outcome for {okey}")
            seen["outcome"].add(okey)
            outcomes.append(OutcomeFact(r["source"].strip(), _period(r["period"]),
                                        r["scope_type"].strip(), r["scope_id"].strip(),
                                        r["kpi_name"].strip(), float(r["kpi_value"])))
        except (ValueError, KeyError) as e:
            reject("outcome_facts.csv", r.get("scope_id", "?"), str(e))

    counts = {
        "vendors": len(vendors), "contracts": len(contracts), "fx_rates": len(fx_rates),
        "commitment_lines": len(lines), "rate_components": sum(len(l.components) for l in lines),
        "usage_facts": len(usage), "invoice_lines": len(invoices), "outcomes": len(outcomes),
        "rejected": len(rejected),
    }
    report = {"source": meta["source"], "data_as_of": meta["data_as_of"],
              "freshness": {"age_days": age, "stale": stale, "sla_days": config.max_data_age_days},
              "counts": counts, "rejected": rejected}
    (d / "validation_report.json").write_text(json.dumps(report, indent=2))

    ds = Dataset(vendors, contracts, fx_rates, lines, usage, invoices, outcomes, meta,
                 vendors_by_id, vendors_by_ref, lines_by_id)
    return {"dataset": ds, "report": report}
