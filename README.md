# 💸 ai-spend-control

**Buy-side SaaS + AI spend control — turn consumption into cost, budget, and value.**

![python](https://img.shields.io/badge/python-3.11%2B-blue)
![streamlit](https://img.shields.io/badge/UI-Streamlit-e11d48)
![engine](https://img.shields.io/badge/engine-deterministic%20·%20grounded%20advisor-111)
![tests](https://img.shields.io/badge/tests-169%20passing-brightgreen)

When you *buy* SaaS + AI, usage-based pricing means the bill moves every month and a signed contract
guarantees nothing. This ties **consumption → cost → budget → value** and surfaces the money you're
**wasting, over-paying, or paying for and not using** — before the invoice does. The costing engine is
deterministic; the only LLM (an advisor) is fenced by a grounding critic that can't invent a number.

> Part of the **murmur.red** portfolio · sibling project → [**saas-churn-engine**](https://github.com/murmur-red/saas-churn-engine) (the sell-side of the same coin).

---

## What it does

- **Company control tower** — drill spend **total → department → team → person** to find *who's
  draining* (heavy token users vs the team median), *who's paying but not using* (idle seats & budgets),
  and *where the gaps are* (unowned spend). Each finding rolls up into ranked action items.
- **Connect your data (securely)** — export a usage/billing **CSV**, auto-map your columns, and the full
  pipeline runs on *your* numbers. No database credentials, no PII required; bad rows are **rejected with
  a reason, never faked**. (See [Security](#security).)
- **The pipeline** — `ingest → reconcile → rate → metrics → advisor`:
  - **reconcile** — match usage to commitments, exception-first (unmatched vendor, uncommitted/shadow
    usage, ambiguous commitment).
  - **rate** — coverage-then-meter costing with **FEFO** prepaid drawdown, reserved capacity, tiered
    usage, overage, and FX conversion.
  - **metrics** — billing variance (audit the invoice), waste, budget-breach forecast, commitment
    utilization, cost-per-outcome, spend allocation, upcoming renewals, and WoW/MoM/QoQ spike alerts.
- **Grounded LLM advisor** — turns the metrics into findings + renegotiation actions; a deterministic
  **grounding critic rejects any figure not already on the page**, and it only ever sees aggregated
  totals, never raw rows. Runs on **your own API key** (bring-your-own).
- **Streamlit dashboard** in the murmur.red theme, organized by the FinOps framework
  (visibility → optimization → business alignment).

## Security

The public "connect your data" path is safe by construction:

- **No credentials, no live SQL against your systems** — you export a CSV; nothing connects back in.
- **In-memory only** — rows are never stored, logged, or shared, and are gone on refresh.
- **No PII needed** — only *vendor · period · unit · units · cost · team/project*; strip names first.
- **Injection-inert** — uploaded values are treated as data, never executed (covered by tests).
- **The advisor is off until you click it**, runs on **your** key, and sees only aggregates.

## Quickstart

```bash
pip install -r requirements.txt

streamlit run app.py            # launch the dashboard (opens on the company control tower)

# tests — 169 assertions, no network (the LLM is mocked)
python test_rate.py             # costing engine: schemes, tiers, FEFO, FX (22)
python test_reconcile.py        # usage → commitment matching + exceptions (12)
python test_company.py          # drains / dead-spend / gaps / actions (15)
python test_warehouse_ingest.py # the secure CSV connect path, incl. rejection + injection (10)
python test_advisor.py          # grounding critic rejects hallucinated figures (12)
```

## Layout

| File | Role |
|---|---|
| `app.py` | Streamlit dashboard — company control tower + secure connect-your-data. |
| `models.py` | Hierarchical schema: Vendor → Contract → CommitmentLine → RateComponent; Usage / Invoice / Outcome / Fx. |
| `reconcile.py` | Exception-first matching of usage to commitments. |
| `rate.py` | Coverage-then-meter costing engine (FEFO prepaid, reserved, tiered, overage, FX). |
| `metrics.py` | Billing variance, waste, budget-breach, utilization, cost-per-outcome, allocation, renewals. |
| `company.py` | Org-level allocation vs usage → drains, dead spend, gaps, action items. |
| `warehouse_ingest.py` | Build a full dataset from an uploaded CSV — validate/reject rows, derive rates from cost. |
| `advisor.py` | LLM advisor + deterministic grounding critic (bring-your-own key). |
| `actions.py` / `action_sinks.py` | Turn findings into actions; dispatch to Slack / ticket / email / calendar (approval-gated). |
| `core/` | Vendored numeric primitives (parity-guarded against `saas-churn-engine/core`). |

## Status

A **prototype on synthetic data**, built to demonstrate the architecture. Rates derived from an
uploaded `cost` column reproduce the real bill; without one, usage is shown **un-priced** (flagged, not
faked). Contract-term extraction from PDFs is on the roadmap.

---

<sub>Sibling project: [saas-churn-engine](https://github.com/murmur-red/saas-churn-engine).</sub>
