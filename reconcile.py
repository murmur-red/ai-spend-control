"""reconcile() — match each UsageFact to the CommitmentLine it consumed against.

Pure, deterministic, exception-first (frozen architecture). Runs AFTER ingest; sets
UsageFact.commitment_line_id or routes the fact to a first-class exception. It does NOT assume the
adapter's guarantees (defensive: a dataset could come from elsewhere), so it re-checks the vendor.

Selection:
- candidates = active lines for the fact's vendor whose [effective_from, effective_to] window
  contains the fact's period AND whose (alias-normalized) model matches, where a line with an empty
  model_or_endpoint is a vendor-wide wildcard.
- most-specific wins: an exact-model candidate beats a wildcard; a remaining tie is AMBIGUOUS.
- zero candidates ⇒ UNCOMMITTED_USAGE (real spend with no contract — a FinOps signal).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models import Dataset


def _normalize(model: str, aliases: dict[str, str]) -> str:
    return aliases.get(model, model)


def reconcile(dataset: Dataset, config, report_path: str | None = None) -> dict[str, Any]:
    aliases = getattr(config, "model_aliases", {}) or {}
    contracts_by_id = {c.id: c for c in dataset.contracts}

    # Index active lines by vendor (via contract).
    lines_by_vendor: dict[str, list] = {}
    for ln in dataset.commitment_lines:
        contract = contracts_by_id.get(ln.contract_id)
        if contract:
            lines_by_vendor.setdefault(contract.vendor_id, []).append(ln)

    exceptions: list[dict[str, Any]] = []
    matched = 0

    for u in dataset.usage_facts:
        if u.vendor_id not in dataset.vendors_by_id:
            u.commitment_line_id = None
            exceptions.append({"usage_id": u.id, "type": "UNMATCHED_VENDOR", "vendor_id": u.vendor_id})
            continue

        fmodel = _normalize(u.model_or_endpoint, aliases)
        candidates = []
        for ln in lines_by_vendor.get(u.vendor_id, []):
            if not (ln.effective_from <= u.period <= ln.effective_to):
                continue
            if ln.model_or_endpoint == "" or _normalize(ln.model_or_endpoint, aliases) == fmodel:
                candidates.append(ln)

        if not candidates:
            u.commitment_line_id = None
            exceptions.append({"usage_id": u.id, "type": "UNCOMMITTED_USAGE",
                               "model_or_endpoint": u.model_or_endpoint})
            continue

        # Specificity: exact-model candidates beat vendor-wide wildcards.
        exact = [ln for ln in candidates
                 if ln.model_or_endpoint != "" and _normalize(ln.model_or_endpoint, aliases) == fmodel]
        chosen = exact if exact else candidates

        if len(chosen) == 1:
            u.commitment_line_id = chosen[0].id
            matched += 1
        else:
            u.commitment_line_id = None
            exceptions.append({"usage_id": u.id, "type": "AMBIGUOUS_COMMITMENT",
                               "candidates": sorted(ln.id for ln in chosen)})

    by_type: dict[str, int] = {}
    for e in exceptions:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    report = {"matched": matched, "exceptions_by_type": by_type, "exceptions": exceptions}

    out = Path(report_path or (Path(config.data_dir) / "reconcile_report.json"))
    out.write_text(json.dumps(report, indent=2))
    return report
