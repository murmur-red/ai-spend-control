"""StripeConnector — pull a Stripe resource (Subscription, Invoice, …) through an injected client.

`client`: in prod the `stripe` module (API key configured); in tests a fake. Expects
`getattr(client, resource).list(**params)` to return a Stripe ListObject (`.data`) or `{"data": [...]}`.
Injected client → no SDK import here, fully mockable, encodes only Stripe's contract.
"""
from __future__ import annotations

from typing import Any, Optional


class StripeConnector:
    def __init__(self, client: Any, resource: str = "Subscription", params: Optional[dict] = None):
        self._client = client
        self._resource = resource
        self._params = params or {}

    def fetch(self) -> list[dict[str, Any]]:
        listing = getattr(self._client, self._resource).list(**self._params)
        rows = listing["data"] if isinstance(listing, dict) else getattr(listing, "data", [])
        return [dict(r) for r in rows]
