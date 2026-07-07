"""TableauConnector — pull rows from a published Tableau data source through an injected client.

`client`: in prod a thin wrapper over Tableau's VizQL Data Service / REST API (exposing
`query_datasource(datasource_id, query) -> list[dict]`); in tests a fake. Injected client → no SDK
import here, mockable, encodes only Tableau's contract.
"""
from __future__ import annotations

from typing import Any, Optional


class TableauConnector:
    def __init__(self, client: Any, datasource_id: str, query: Optional[dict] = None):
        self._client = client
        self._datasource_id = datasource_id
        self._query = query

    def fetch(self) -> list[dict[str, Any]]:
        rows = self._client.query_datasource(self._datasource_id, self._query)
        return [dict(r) for r in rows]
