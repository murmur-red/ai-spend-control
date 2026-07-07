"""SQLConnector — pull rows from any warehouse/DB via a standard DB-API 2.0 connection.

Works with sqlite3 (stdlib, used in tests), psycopg2 (Postgres/Redshift), snowflake-connector-python,
google-cloud-bigquery's DB-API, etc. — the customer supplies a connection (or connection string) and a
SQL query/view that returns one row per account. No manual data entry; nothing vendor-specific here.
"""
from __future__ import annotations

from typing import Any


class SQLConnector:
    def __init__(self, connection: Any, query: str):
        self._conn = connection
        self._query = query

    def fetch(self) -> list[dict[str, Any]]:
        cur = self._conn.cursor()
        try:
            cur.execute(self._query)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()
