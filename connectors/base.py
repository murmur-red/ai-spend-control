"""Connector interface — the ingestion seam. A connector returns raw source rows (list of dicts)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Connector(Protocol):
    def fetch(self) -> list[dict[str, Any]]:
        """Return raw rows from the source system (each a column→value dict)."""
        ...
