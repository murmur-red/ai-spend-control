"""Build UsageFact records from connector rows (warehouse usage → the spend pipeline).

Usage/consumption is what actually lives in a customer's warehouse; contract terms live elsewhere.
So the connect-a-warehouse path pulls USAGE, maps it to our schema, validates each row (bad rows are
rejected individually), and the rest of the dataset (vendors/contracts/commitments) stays as loaded.
reconcile() then matches the pulled usage to commitments and rate()/metrics recompute.
"""
from __future__ import annotations

import re
from typing import Any

from models import UsageFact, UNIT_TYPES

_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def usage_from_rows(rows: list[dict[str, Any]]) -> tuple[list[UsageFact], list[dict[str, str]]]:
    """Returns (usage_facts, rejected). Rows are already mapped to our field names."""
    facts: list[UsageFact] = []
    rejected: list[dict[str, str]] = []
    for i, r in enumerate(rows, start=1):
        try:
            vendor_id = str(r.get("vendor_id", "")).strip()
            if not vendor_id:
                raise ValueError("missing vendor_id")
            period = str(r.get("period", "")).strip()
            if not _PERIOD.match(period):
                raise ValueError(f"bad period '{period}' (want YYYY-MM)")
            unit_type = str(r.get("unit_type", "")).strip()
            if unit_type not in UNIT_TYPES:
                raise ValueError(f"bad unit_type '{unit_type}'")
            units = float(r.get("units_consumed"))
            if units < 0:
                raise ValueError("units_consumed < 0")
            facts.append(UsageFact(
                id=str(r.get("id") or f"wh-{i}"),
                source=str(r.get("source", "warehouse")),
                source_vendor_ref=str(r.get("source_vendor_ref", "")),
                vendor_id=vendor_id, period=period,
                model_or_endpoint=str(r.get("model_or_endpoint", "")),
                unit_type=unit_type, units_consumed=units,
                team_id=str(r.get("team_id", "")), cost_center=str(r.get("cost_center", "")),
                project_id=str(r.get("project_id", "")),
            ))
        except (KeyError, ValueError, TypeError) as e:
            rejected.append({"row": str(i), "reason": str(e)})
    return facts, rejected
