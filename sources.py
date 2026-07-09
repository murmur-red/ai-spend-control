"""Shared data-source helpers: read an uploaded CSV/Excel, or fetch a public Google Sheet as CSV.

Safe by design: file parsing is in-memory; the Google Sheets fetch is restricted to docs.google.com
spreadsheet URLs (no arbitrary server-side fetch / SSRF) and uses the public CSV export endpoint —
no login, no API key. Set the sheet to "anyone with the link can view".
"""
from __future__ import annotations

import io
import re
import urllib.request

import pandas as pd

_GS_ID = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)")
_GS_GID = re.compile(r"[#&?]gid=([0-9]+)")


def read_upload(uploaded) -> pd.DataFrame:
    """Read a Streamlit UploadedFile into a DataFrame — Excel (.xlsx/.xls) or CSV, by extension."""
    name = (getattr(uploaded, "name", "") or "").lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded, dtype=str).fillna("")
    return pd.read_csv(uploaded, dtype=str).fillna("")


def gsheet_export_url(url: str) -> str:
    """Validate a public Google Sheets URL → its CSV export URL. Raises ValueError for anything that
    isn't a docs.google.com spreadsheet link (prevents arbitrary server-side fetches)."""
    url = (url or "").strip()
    m = _GS_ID.match(url)
    if not m:
        raise ValueError("Not a Google Sheets link. Paste a https://docs.google.com/spreadsheets/… URL "
                         "shared as 'anyone with the link can view'.")
    sheet_id = m.group(1)
    gid_m = _GS_GID.search(url)
    gid = gid_m.group(1) if gid_m else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def fetch_gsheet(url: str, timeout: int = 15) -> pd.DataFrame:
    """Fetch a public Google Sheet as a DataFrame via its CSV export endpoint."""
    export = gsheet_export_url(url)
    req = urllib.request.Request(export, headers={"User-Agent": "murmur-data/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (host is validated above)
        raw = r.read()
    df = pd.read_csv(io.BytesIO(raw), dtype=str).fillna("")
    if df.empty or len(df.columns) == 0:
        raise ValueError("That sheet looks empty, or it isn't shared publicly "
                         "(set it to 'anyone with the link can view').")
    return df
