"""AnthropicUsageConnector — pull real org spend from the Anthropic Admin Cost/Usage API.

Needs an ADMIN API key (`sk-ant-admin…`) — the regular app key returns 401 on these org endpoints.
Injected `http` (a callable `(url, headers) -> parsed_json`) keeps it testable without a key; in prod
it defaults to a urllib GET. `fetch()` flattens the cost report into flat rows:
    {period: "YYYY-MM", vendor: "Anthropic", model_or_endpoint, cost, currency}
which map cleanly onto invoices / a spend ledger.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable, Optional

COST_URL = "https://api.anthropic.com/v1/organizations/cost_report"


def _default_http(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


class AnthropicUsageConnector:
    def __init__(self, admin_key: str, starting_at: str, ending_at: Optional[str] = None,
                 http: Optional[Callable[[str, dict], dict]] = None):
        self._key = admin_key
        self._start = starting_at            # ISO8601, e.g. "2026-06-01T00:00:00Z"
        self._end = ending_at
        self._http = http or _default_http

    def fetch(self) -> list[dict[str, Any]]:
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
        url = f"{COST_URL}?starting_at={self._start}" + (f"&ending_at={self._end}" if self._end else "")
        payload = self._http(url, headers)

        rows: list[dict[str, Any]] = []
        for bucket in payload.get("data", []):
            period = str(bucket.get("starting_at", ""))[:7]   # YYYY-MM
            for res in bucket.get("results", []):
                amount = res.get("amount")
                if amount is None:
                    continue
                rows.append({
                    "period": period,
                    "vendor": "Anthropic",
                    "model_or_endpoint": res.get("model", "") or "",
                    "cost": float(amount),
                    "currency": res.get("currency", "USD"),
                })
        return rows
