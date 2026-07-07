#!/usr/bin/env python3
"""
Tests for reconcile() (piece 2): matching, model aliasing, specificity, and the exception paths
(UNCOMMITTED_USAGE, AMBIGUOUS_COMMITMENT). Run: python test_reconcile.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from config import SpendConfig
from adapter import load_dataset
from reconcile import reconcile

passed = failed = 0


def check(label, got, want):
    global passed, failed
    if got == want:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")


def _tmp():
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree("data", tmp / "data")
    return tmp


def _links(ds):
    return {u.id: u.commitment_line_id for u in ds.usage_facts}


def main() -> None:
    # ── Happy path on the real dataset ──────────────────────────────────────
    ds = load_dataset(SpendConfig())["dataset"]
    rep = reconcile(ds, SpendConfig(), report_path="/tmp/reconcile.json")
    m = _links(ds)
    check("u1→l1", m["u1"], "l1")
    check("u2→l1", m["u2"], "l1")
    check("u3→l2 (wildcard)", m["u3"], "l2")
    check("matched", rep["matched"], 3)
    check("no_exceptions", len(rep["exceptions"]), 0)

    # ── Aliasing: gpt-4-0613 normalizes to gpt-4 → matches l1 ───────────────
    t = _tmp()
    (t / "data" / "usage_facts.csv").open("a").write(
        "uA,billing-api,llmcloud,v1,2026-06,gpt-4-0613,TOKEN,1000,teamA,cc-eng,projX\n")
    cfg = SpendConfig(data_dir=str(t / "data"), model_aliases={"gpt-4-0613": "gpt-4"})
    ds = load_dataset(cfg)["dataset"]; reconcile(ds, cfg)
    check("alias_match", _links(ds)["uA"], "l1")
    shutil.rmtree(t)

    # ── Uncommitted: unknown model, no line ─────────────────────────────────
    t = _tmp()
    (t / "data" / "usage_facts.csv").open("a").write(
        "uU,billing-api,llmcloud,v1,2026-06,claude-3,TOKEN,1000,teamA,cc-eng,projX\n")
    cfg = SpendConfig(data_dir=str(t / "data"))
    ds = load_dataset(cfg)["dataset"]; rep = reconcile(ds, cfg)
    exU = [e for e in rep["exceptions"] if e["usage_id"] == "uU"]
    check("uncommitted_type", exU[0]["type"] if exU else None, "UNCOMMITTED_USAGE")
    check("uncommitted_unset", _links(ds)["uU"], None)
    shutil.rmtree(t)

    # ── Ambiguous: a second gpt-4 line overlapping → u1,u2 ambiguous ────────
    t = _tmp()
    (t / "data" / "commitment_lines.csv").open("a").write("l1b,c1,gpt-4,2026-01,2026-12\n")
    cfg = SpendConfig(data_dir=str(t / "data"))
    ds = load_dataset(cfg)["dataset"]; rep = reconcile(ds, cfg)
    amb = [e for e in rep["exceptions"] if e["type"] == "AMBIGUOUS_COMMITMENT"]
    check("ambiguous_count", len(amb), 2)
    check("ambiguous_candidates", sorted(amb[0]["candidates"]), ["l1", "l1b"])
    check("ambiguous_unset", _links(ds)["u1"], None)
    shutil.rmtree(t)

    # ── Specificity: exact model beats a vendor-wide wildcard ───────────────
    t = _tmp()
    (t / "data" / "commitment_lines.csv").open("a").write("lwild,c1,,2026-01,2026-12\n")
    cfg = SpendConfig(data_dir=str(t / "data"))
    ds = load_dataset(cfg)["dataset"]; reconcile(ds, cfg)
    check("specificity_exact", _links(ds)["u1"], "l1")   # not lwild
    shutil.rmtree(t)

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
