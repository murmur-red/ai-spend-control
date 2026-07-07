"""FieldMapping — map a customer's source columns onto our signal schema, once.

A customer's warehouse/CRM uses their own column names (annual_value, renews_on, csm_owner…). The
mapping says `our_field -> their_column`, with optional per-field value transforms. Any of our fields
left unmapped (or NULL in the source) is simply absent → that signal is unavailable → coverage drops.
Nothing is invented.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


class FieldMapping:
    def __init__(self, mapping: dict[str, str], transforms: Optional[dict[str, Callable[[Any], Any]]] = None):
        self.mapping = mapping                    # our_field -> source_column
        self.transforms = transforms or {}
        # A source column can't feed two of our fields (ambiguous) — caught early.
        seen: dict[str, str] = {}
        for our_field, src in mapping.items():
            if src in seen:
                raise ValueError(f"source column '{src}' mapped to both '{seen[src]}' and '{our_field}'")
            seen[src] = our_field

    def apply(self, rows: list[dict[str, Any]], constants: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Transform raw source rows into our-schema account dicts. `constants` (e.g. source,
        data_as_of) are added to every row."""
        out = []
        for r in rows:
            acct: dict[str, Any] = dict(constants or {})
            for our_field, src in self.mapping.items():
                if src in r and r[src] is not None and r[src] != "":
                    v = r[src]
                    if our_field in self.transforms:
                        v = self.transforms[our_field](v)
                    acct[our_field] = v
            out.append(acct)
        return out
