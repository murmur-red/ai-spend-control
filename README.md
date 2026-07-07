# ai-spend-control — buy-side SaaS + AI spend control (Product 2)

Helps a company that **buys** SaaS + AI (token) services understand what that spend actually costs:
consumption → COGS → margin → cost-per-outcome, with budget-breach forecasting and renegotiation
levers. Standalone folder, vendored `core/` (kept byte-identical to `~/saas-churn-engine/core`).

## Build status (each piece gated by Grok + Gemini, dual-PASS required)
| Piece | What | Status |
|---|---|---|
| 1 · Data model + adapter | Typed schema + ingest validation + data contract + audit | ✅ dual-PASS |
| 2 · reconcile() | Match usage → commitment (exception-first) | ✅ dual-PASS |
| 3 · rate() | Coverage-then-meter, FEFO prepaid | ✅ dual-PASS |
| 4 · metrics | Billing variance, waste, budget-breach, cost-per-outcome | ✅ dual-PASS |
| 5 · LLM advisor | Explains rated numbers (the only LLM in either product) | ✅ dual-PASS |

**All 5 pieces dual-PASSed.** The advisor runs on `claude-sonnet-5` with a deterministic grounding critic (every finding must cite an exact metric; hallucinated figures are rejected).

## Layout
| File | Role |
|---|---|
| `models.py` | Hierarchical schema: Vendor → Contract → CommitmentLine → RateComponent; UsageFact / InvoiceLine / OutcomeFact / FxRate. |
| `config.py` | `SpendConfig` — explicit `today`, base currency, freshness SLA. |
| `adapter.py` | Ingest gate: schema/enum/params/referential + uniqueness + non-empty-key validation, stale hard-fail, per-row rejection, `data/validation_report.json` audit. |
| `data/` | Synthetic relational dataset (CSVs + `rate_components.json` + `meta.json`). |
| `core/` | Vendored numeric primitives (parity-guarded). |

## Run
```bash
python test_characterization.py    # data model + adapter (20 assertions, incl. validation rejections)
python test_reconcile.py           # reconcile() matching + exceptions (12 assertions)
python test_rate.py                # rate() costing engine: schemes, tiers, FEFO, FX (22 assertions)
python test_metrics.py             # billing variance, waste, budget-breach, cost-per-outcome (14 assertions)
python test_advisor.py             # LLM advisor: grounding critic, hallucination rejection (12 assertions, LLM mocked)
python test_core_parity.py         # fails if vendored core drifts from ~/saas-churn-engine/core

# live advisor smoke (real claude-sonnet-5 call):
RUN_LIVE_ADVISOR=1 python test_advisor.py
```
