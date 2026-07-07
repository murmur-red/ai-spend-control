#!/usr/bin/env python3
"""
Tests for AnthropicUsageConnector — uses an injected fake HTTP client so it's verified without an
admin key or network. Run: python test_anthropic_usage.py
"""
from __future__ import annotations

import sys

from connectors.base import Connector
from connectors.anthropic_usage import AnthropicUsageConnector

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


def _fake_http(url, headers):
    # Shaped like the Anthropic Admin cost_report response.
    check("admin key sent as x-api-key", headers.get("x-api-key") == "sk-ant-admin-TEST")
    check("query targets the cost_report endpoint", "organizations/cost_report" in url)
    return {"data": [
        {"starting_at": "2026-06-01T00:00:00Z", "ending_at": "2026-07-01T00:00:00Z", "results": [
            {"amount": "812.40", "currency": "USD", "model": "claude-sonnet-5"},
            {"amount": "133.10", "currency": "USD", "model": "claude-opus-4-8"},
        ]},
        {"starting_at": "2026-07-01T00:00:00Z", "ending_at": "2026-08-01T00:00:00Z", "results": [
            {"amount": "410.00", "currency": "USD", "model": "claude-sonnet-5"},
            {"amount": None},   # malformed result → skipped, not crash
        ]},
    ]}


def main() -> None:
    conn = AnthropicUsageConnector("sk-ant-admin-TEST", starting_at="2026-06-01T00:00:00Z", http=_fake_http)
    check("is a Connector", isinstance(conn, Connector))
    rows = conn.fetch()

    check("flattened 3 valid cost rows (malformed skipped)", len(rows) == 3)
    r0 = rows[0]
    check("period is YYYY-MM", r0["period"] == "2026-06")
    check("vendor tagged Anthropic", r0["vendor"] == "Anthropic")
    check("model + cost extracted", r0["model_or_endpoint"] == "claude-sonnet-5" and r0["cost"] == 812.40)
    total = sum(r["cost"] for r in rows)
    check("total cost summed (812.40 + 133.10 + 410.00)", abs(total - 1355.50) < 1e-6)

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
