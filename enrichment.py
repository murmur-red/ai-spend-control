"""Enrichment — fill known data gaps from 3rd-party sources, without overwriting first-party data.

Some signals are rarely in a customer's own systems (e.g. company-health events like layoffs / M&A).
An enrichment provider supplies these keyed by account; we fill ONLY where the field is missing, so
first-party data always wins. In production a provider calls a news/firmographics API (Clearbit,
etc.); the interface is the same. Every fill is recorded so the source of each value is auditable.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


def enrich(accounts: list[dict[str, Any]], field: str,
           provider: Callable[[str], Optional[Any]], key: str = "account_name") -> dict[str, int]:
    """Fill `accounts[*][field]` from `provider(account_key)` where currently absent. Returns a small
    audit summary {filled, skipped_present, skipped_no_value}."""
    filled = present = no_value = 0
    for a in accounts:
        cur = a.get(field)
        if cur is not None and cur != "":
            present += 1
            continue
        val = provider(a.get(key, ""))
        if val is None:
            no_value += 1
            continue
        a[field] = val
        a.setdefault("_enriched", []).append(field)
        filled += 1
    return {"filled": filled, "skipped_present": present, "skipped_no_value": no_value}


def static_provider(source: dict[str, Any]) -> Callable[[str], Optional[Any]]:
    """Wrap a plain dict {account_key: value} as an enrichment provider (used for tests/demos)."""
    return lambda k: source.get(k)
