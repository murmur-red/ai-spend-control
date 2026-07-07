"""SalesforceConnector — pull rows via SOQL through an injected Salesforce client.

`client`: in prod a `simple_salesforce.Salesforce` instance (or anything exposing
`query_all(soql) -> {"records": [...]}`); in tests, a fake. The client is INJECTED, so this module
imports no SDK, stays dependency-free/testable, and only encodes Salesforce's API contract.
"""
from __future__ import annotations

from typing import Any


class SalesforceConnector:
    def __init__(self, client: Any, soql: str):
        self._client = client
        self._soql = soql

    def fetch(self) -> list[dict[str, Any]]:
        result = self._client.query_all(self._soql)
        records = result.get("records", []) if isinstance(result, dict) else getattr(result, "records", [])
        # Drop Salesforce's per-record `attributes` metadata; return flat column→value rows.
        return [{k: v for k, v in rec.items() if k != "attributes"} for rec in records]
