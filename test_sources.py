#!/usr/bin/env python3
"""Regression tests for data-source helpers (sources.py). No live network. Run: python test_sources.py"""
import io
import sys
import urllib.error
import urllib.request

import sources

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1
    else: failed += 1; print(f"  FAIL  {label}")


def main():
    # read_upload: CSV and Excel by extension
    csv = io.BytesIO(b"vendor,cost\nAcme,10\n"); csv.name = "x.csv"
    df = sources.read_upload(csv)
    check("reads CSV", list(df.columns) == ["vendor", "cost"] and len(df) == 1)

    # gsheet URL validation
    check("valid gsheet → export url",
          sources.gsheet_export_url("https://docs.google.com/spreadsheets/d/AB_c1/edit#gid=7")
          == "https://docs.google.com/spreadsheets/d/AB_c1/export?format=csv&gid=7")
    check("defaults gid=0", sources.gsheet_export_url("https://docs.google.com/spreadsheets/d/AB_c1").endswith("gid=0"))
    for bad in ["https://evil.com/x", "http://docs.google.com/spreadsheets/d/x", "not a url", ""]:
        try:
            sources.gsheet_export_url(bad); check(f"rejects {bad!r}", False)
        except ValueError:
            check(f"rejects {bad!r}", True)

    # fetch_gsheet: parse, empty, HTML-signin, HTTP error → all friendly ValueErrors
    orig = urllib.request.urlopen
    class _R:
        def __init__(s, b): s._b = b
        def read(s): return s._b
        def __enter__(s): return s
        def __exit__(s, *a): return False

    urllib.request.urlopen = lambda *a, **k: _R(b"vendor,period\nAcme,2026-06\n")
    df = sources.fetch_gsheet("https://docs.google.com/spreadsheets/d/X/edit")
    check("fetch_gsheet parses valid CSV", len(df) == 1 and "vendor" in df.columns)

    for label, body in [("empty", b""), ("html", b"<!DOCTYPE html><html>login</html>")]:
        urllib.request.urlopen = lambda *a, **k: _R(body)
        try:
            sources.fetch_gsheet("https://docs.google.com/spreadsheets/d/X/edit"); check(f"{label} raises", False)
        except ValueError:
            check(f"{label} → friendly ValueError", True)

    def _boom(*a, **k): raise urllib.error.HTTPError("u", 404, "nf", {}, None)
    urllib.request.urlopen = _boom
    try:
        sources.fetch_gsheet("https://docs.google.com/spreadsheets/d/X/edit"); check("404 raises", False)
    except ValueError:
        check("HTTP 404 → friendly ValueError", True)
    urllib.request.urlopen = orig

    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
