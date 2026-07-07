#!/usr/bin/env python3
"""
Core-parity guard — the vendored core/ here MUST stay byte-identical to the canonical copy in
~/saas-churn-engine/core. This is the safeguard for the deliberate decision to vendor core (each
product standalone): it fails loudly if the copies ever drift. Run: python test_core_parity.py
"""
from __future__ import annotations

import filecmp
import sys
from pathlib import Path

HERE = Path(__file__).parent / "core"
CANON = Path.home() / "saas-churn-engine" / "core"


def main() -> None:
    if not CANON.exists():
        print(f"  SKIP  canonical core not found at {CANON} (sibling product absent)")
        print("\n0/0 passed (skipped)")
        return

    passed = failed = 0
    files = sorted(p.name for p in HERE.glob("*.py"))
    canon_files = sorted(p.name for p in CANON.glob("*.py"))
    if files != canon_files:
        failed += 1
        print(f"  FAIL  file set differs: {files} vs {canon_files}")
    for name in files:
        if filecmp.cmp(HERE / name, CANON / name, shallow=False):
            passed += 1
        else:
            failed += 1
            print(f"  FAIL  core/{name} drifted from canonical ~/saas-churn-engine/core/{name}")

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
