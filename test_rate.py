#!/usr/bin/env python3
"""
Tests for rate() (piece 3) — the coverage-then-meter costing engine. Covers each scheme, tier modes,
FEFO prepaid across periods, expiry waste, FX-on-params, and the real l1/l2 fold. Run: python test_rate.py
"""
from __future__ import annotations

import sys

from config import SpendConfig
from adapter import load_dataset
from reconcile import reconcile
from models import CommitmentLine, RateComponent, Scheme
from rate import rate, rate_line_over_periods, price_tiers

passed = failed = 0


def check(label, got, want, tol=1e-6):
    global passed, failed
    ok = (got == want) if not isinstance(want, float) else (got is not None and abs(got - want) <= tol)
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")


def line(*comps):
    return CommitmentLine("L", "C", "gpt-4", "2026-01", "2026-12", list(comps))


def comp(scheme, params, order=1, cid="c"):
    return RateComponent(cid, "L", order, scheme, params)


def one(ln, period="2026-06", usage=None, state=None, fx=1.0, trailing=None):
    return rate(ln, period, usage or {}, state or {}, fx, trailing or {})


def main() -> None:
    # ── Real dataset fold: l1 (prepaid → tiered) and l2 (per-seat) ──────────
    ds = load_dataset(SpendConfig())["dataset"]
    reconcile(ds, SpendConfig())
    l1 = ds.lines_by_id["l1"]
    r = rate_line_over_periods(l1, ["2026-05", "2026-06"],
                               {"2026-05": {"TOKEN": 2_500_000}, "2026-06": {"TOKEN": 4_000_000}},
                               {"2026-05": 1.0, "2026-06": 1.0})
    check("l1 May expected (prepaid covers)", r[0].expected_cost, 0.0)
    check("l1 May prepaid remaining", r[0].prepaid_remaining_total, 500_000.0)
    check("l1 Jun expected (2M@.03 + 1.5M@.02)", r[1].expected_cost, 90000.0)
    check("l1 Jun prepaid drained", r[1].prepaid_remaining_total, 0.0)
    check("l1 Jun no waste", r[1].waste_cost, 0.0)

    l2 = ds.lines_by_id["l2"]
    r2 = rate_line_over_periods(l2, ["2026-06"], {"2026-06": {"SEAT": 80}}, {"2026-06": 1.0})
    check("l2 per-seat expected (80×50)", r2[0].expected_cost, 4000.0)

    # ── price_tiers unit behavior ───────────────────────────────────────────
    tiers = [[2_000_000, 0.03], [10_000_000, 0.02]]
    check("marginal 3M", price_tiers(3_000_000, tiers, "MARGINAL")[0], 60000.0 + 20000.0)
    check("volume 3M (all @ .02)", price_tiers(3_000_000, tiers, "VOLUME")[0], 60000.0)
    check("volume 1.5M (all @ .03)", price_tiers(1_500_000, tiers, "VOLUME")[0], 45000.0)

    # ── FLAT_FEE + FX (fee halved) ──────────────────────────────────────────
    res, _ = one(line(comp(Scheme.FLAT_FEE, {"period_fee": 1000})), fx=0.5)
    check("flat fee fx=0.5", res.expected_cost, 500.0)

    # ── RESERVED_CAPACITY: pay for reserved; utilization ───────────────────
    rl = line(comp(Scheme.RESERVED_CAPACITY, {"unit_type": "TOKEN", "reserved_units": 1_000_000, "reserved_rate": 0.01}))
    res, _ = one(rl, usage={"TOKEN": 600_000})
    check("reserved cost", res.expected_cost, 10000.0)
    check("reserved utilization", res.reserved_utilization["c"], 0.6)
    check("reserved covers usage", res.billable_by_type["TOKEN"], 0.0)
    check("reserved waste (40% unused × $10k)", res.reserved_waste_cost, 4000.0)

    # ── TIERED + OVERAGE beyond top tier ────────────────────────────────────
    tl = line(comp(Scheme.TIERED_USAGE, {"unit_type": "TOKEN", "tiers": tiers, "tier_mode": "MARGINAL"}, 1, "t"),
              comp(Scheme.OVERAGE, {"unit_type": "TOKEN", "overage_rate": 0.04}, 2, "o"))
    res, _ = one(tl, usage={"TOKEN": 11_000_000})  # 2M@.03 + 8M@.02 + 1M@.04
    check("tiered+overage cost", res.expected_cost, 60000.0 + 160000.0 + 40000.0)
    check("overage units", res.overage_units, 1_000_000.0)

    # ── FEFO across two prepaid buckets + expiry waste ─────────────────────
    fl = line(comp(Scheme.PREPAID_DRAWDOWN, {"unit_type": "TOKEN", "prepaid_units": 1_000_000,
                                             "unit_value": 0.02, "expiry": "2026-05"}, 1, "early"),
              comp(Scheme.PREPAID_DRAWDOWN, {"unit_type": "TOKEN", "prepaid_units": 1_000_000,
                                             "unit_value": 0.02, "expiry": "2026-08"}, 2, "late"))
    # May: usage 600k → drains earliest-expiry bucket first (FEFO); 400k left in 'early' expires → waste
    res, state = one(fl, period="2026-05", usage={"TOKEN": 600_000})
    check("FEFO drains early bucket", state["early"], 400_000.0)
    check("FEFO late untouched", state["late"], 1_000_000.0)
    check("expiry waste (400k×.02)", res.waste_cost, 8000.0)

    # ── Runway: per-unit-type + zero-usage month included in trailing avg ───
    pl = line(comp(Scheme.PREPAID_DRAWDOWN, {"unit_type": "TOKEN", "prepaid_units": 3_000_000,
                                             "unit_value": 0.02}, 1, "p"))
    rr = rate_line_over_periods(pl, ["2026-04", "2026-05", "2026-06"],
                                {"2026-04": {"TOKEN": 300_000}, "2026-05": {}, "2026-06": {"TOKEN": 300_000}},
                                {"2026-04": 1.0, "2026-05": 1.0, "2026-06": 1.0})
    # After Jun: remaining 3M − 600k = 2.4M; trailing avg over Apr(300k)+May(0) = 150k → runway 16.0
    check("runway includes zero month", rr[2].prepaid_runway_by_type["TOKEN"], 16.0)
    check("runway scalar = per-type min", rr[2].prepaid_runway_months, 16.0)
    check("remaining_by_type not mixed", rr[2].prepaid_remaining_by_type["TOKEN"], 2_400_000.0)

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
