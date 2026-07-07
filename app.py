"""Local dashboard for Product 2 — buy-side SaaS+AI spend control. Run:
    streamlit run app.py --server.port 8532
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import streamlit as st

from config import SpendConfig
from reconcile import reconcile
from metrics import (rate_portfolio, billing_variance, total_waste, portfolio_monthly_cost,
                     budget_breach, cost_per_outcome, mom_qoq_cost, spend_allocation,
                     commitment_utilization, upcoming_renewals)
from advisor import build_context, advise
from recommendations import recommendations
from connectors.sql import SQLConnector
from mapping import FieldMapping
from actions import derive_actions
from action_sinks import CalendarSink, dispatch
from warehouse_ingest import dataset_from_usage_rows, REQUIRED_FIELDS, OPTIONAL_FIELDS

st.set_page_config(page_title="ai-spend-control", page_icon="💸", layout="wide")
URGENCY_ICON = {"URGENT": "🚨", "ELEVATED": "🟠", "STRATEGIC": "🔵", "OK": "🟢"}

# Their-schema demo rows (deliberately different column names) — proves the real upload path works.
DEMO_ROWS = [
    {"supplier": "Anthropic", "month": "2026-05", "meter": "TOKEN", "qty": "4200000", "amount": "336", "team": "ml", "project": "assistant", "model": "claude-sonnet-5"},
    {"supplier": "Anthropic", "month": "2026-06", "meter": "TOKEN", "qty": "5100000", "amount": "408", "team": "ml", "project": "assistant", "model": "claude-sonnet-5"},
    {"supplier": "OpenAI", "month": "2026-05", "meter": "TOKEN", "qty": "3000000", "amount": "180", "team": "web", "project": "search", "model": "gpt-4"},
    {"supplier": "OpenAI", "month": "2026-06", "meter": "TOKEN", "qty": "3600000", "amount": "216", "team": "web", "project": "search", "model": "gpt-4"},
    {"supplier": "Snowflake", "month": "2026-06", "meter": "GB", "qty": "1200", "amount": "240", "team": "data", "project": "warehouse", "model": ""},
    {"supplier": "Datadog", "month": "2026-06", "meter": "SEAT", "qty": "40", "amount": "720", "team": "platform", "project": "observability", "model": ""},
]
DEMO_MAPPING = {"vendor": "supplier", "period": "month", "unit_type": "meter", "units_consumed": "qty",
                "cost": "amount", "team_id": "team", "project_id": "project", "model_or_endpoint": "model"}


def _guess_mapping(cols: list[str]) -> dict[str, str]:
    """Best-effort auto-map of the user's columns → our fields (case/synonym-insensitive)."""
    syn = {
        "vendor": ["vendor", "supplier", "provider", "service", "tool", "merchant"],
        "period": ["period", "month", "billing_month", "date", "invoice_month"],
        "unit_type": ["unit_type", "unit", "meter", "usage_type", "metric"],
        "units_consumed": ["units_consumed", "units", "qty", "quantity", "usage", "amount_units"],
        "cost": ["cost", "amount", "spend", "charge", "total", "usd"],
        "team_id": ["team_id", "team", "department", "dept", "group"],
        "project_id": ["project_id", "project", "app", "workload"],
        "model_or_endpoint": ["model_or_endpoint", "model", "endpoint", "sku"],
        "currency": ["currency", "cur", "ccy"],
        "category": ["category", "type", "class"],
    }
    low = {c.lower().strip(): c for c in cols}
    out = {}
    for field, names in syn.items():
        for n in names:
            if n in low:
                out[field] = low[n]
                break
    return out


st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;800&display=swap');
.stApp { background:
    radial-gradient(1100px 500px at 85% -8%, rgba(225,29,72,.06), transparent 60%),
    radial-gradient(900px 420px at 0% 0%, rgba(153,27,27,.04), transparent 55%), #fbf9f9; }
/* Hero */
.mr-badge { font-family:'JetBrains Mono',monospace; font-size:10px; letter-spacing:.28em;
    text-transform:uppercase; color:#b91c1c; background:#fef2f2; border:1px solid #fecaca;
    padding:4px 10px; border-radius:5px; }
.mr-title { font-weight:900; font-size:2.5rem; line-height:1.02; letter-spacing:-.02em; margin:.55rem 0 .35rem;
    background:linear-gradient(90deg,#292524,#b91c1c 60%,#e11d48); -webkit-background-clip:text;
    -webkit-text-fill-color:transparent; }
.mr-sub { color:#57534e; font-size:.95rem; font-weight:400; max-width:52rem; }
.mr-flow { font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.06em; color:#78716c; margin-top:.4rem; }
.mr-flow b { color:#b91c1c; font-weight:600; }
/* Section headers → crimson tick */
.stMarkdown h4, .stMarkdown h3 { font-weight:800 !important; letter-spacing:-.01em; color:#1c1917;
    border-left:3px solid #e11d48; padding-left:.6rem; }
/* Metric cards */
[data-testid="stMetric"] { background:#ffffff; border:1px solid #e7e5e4; border-radius:10px;
    padding:14px 16px; box-shadow:0 1px 2px rgba(28,25,23,.06); }
[data-testid="stMetricLabel"] p { font-family:'JetBrains Mono',monospace; font-size:10px !important;
    letter-spacing:.18em; text-transform:uppercase; color:#78716c !important; }
[data-testid="stMetricValue"] { color:#1c1917; font-weight:800; }
/* Buttons */
.stButton>button, .stDownloadButton>button { background:#b91c1c; color:#fff; border:1px solid rgba(153,27,27,.25);
    border-radius:7px; font-weight:600; letter-spacing:.02em; transition:all .15s; }
.stButton>button:hover, .stDownloadButton>button:hover { background:#dc2626; transform:translateY(-1px);
    box-shadow:0 6px 18px rgba(190,18,60,.25); }
.stButton>button:disabled { background:#e7e5e4; color:#a8a29e; border-color:#e7e5e4; }
/* Cards / alerts / expanders */
[data-testid="stExpander"] { background:#ffffff; border:1px solid #e7e5e4; border-radius:10px; }
[data-testid="stExpander"] summary:hover { color:#b91c1c; }
div[data-testid="stNotification"] { border-radius:9px; border:1px solid #e7e5e4; }
[data-testid="stSidebar"] { background:#faf7f7; border-right:1px solid #eee7e5; }
hr { border-color:#e7e5e4 !important; }
.stCaption, .stCaption p { color:#78716c; }
/* Top nav */
.mr-nav { display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid #e7e5e4; padding:0 2px 12px; margin-bottom:18px; }
.mr-wm { font-weight:900; font-size:1.05rem; letter-spacing:-.02em;
    background:linear-gradient(90deg,#292524,#b91c1c,#e11d48); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.mr-navbadge { font-family:'JetBrains Mono',monospace; font-size:9px; letter-spacing:.22em; text-transform:uppercase;
    color:#b91c1c; background:#fef2f2; border:1px solid #fecaca; padding:3px 8px; border-radius:5px; margin-left:10px; }
.mr-nav-r a { font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.16em; text-transform:uppercase;
    color:#78716c; text-decoration:none; margin-left:22px; transition:color .15s; }
.mr-nav-r a:hover { color:#b91c1c; }
</style>
<div class="mr-nav">
  <div><span class="mr-wm">murmur.red</span><span class="mr-navbadge">🌐 Portfolio · Spend Control</span></div>
  <div class="mr-nav-r">
    <a href="#methodology">Methodology</a>
    <a href="http://localhost:8533" target="_blank">Churn Engine ↗</a>
    <a href="https://github.com/murmur-red/ai-spend-control" target="_blank">GitHub</a>
  </div>
</div>
<div>
  <span class="mr-badge">💸 murmur.red · spend telemetry</span>
  <div class="mr-title">Buy-Side SaaS&nbsp;+&nbsp;AI Spend Control</div>
  <div class="mr-sub">Turn every dollar of consumption into cost, budget, and value — surfacing waste,
     drains, and dead spend before the invoice does.</div>
  <div class="mr-flow">ingest → reconcile → rate <b>(coverage-then-meter · FEFO prepaid)</b> → metrics → grounded LLM advisor</div>
</div>
""", unsafe_allow_html=True)

cfg = SpendConfig()


def render_guide(page: str) -> None:
    """Identical guide UI on every page — an intro banner + a support/docs expander."""
    st.markdown('<div id="methodology"></div>', unsafe_allow_html=True)   # anchor for the Methodology nav link
    st.info("👋 New here? Open the guide — what this is, how to connect your data, what each number "
            "means, and what to do if you don't have some of it.")
    with st.expander("📘 Start here — support & docs", expanded=False):
        st.markdown("""
**What this is.** Spend control for a company that *buys* SaaS + AI. With usage-based pricing the bill
moves every month, so a signed contract guarantees nothing. This ties **consumption → cost → budget →
value** and catches money you're wasting, over-paying, or paying for and not using.
""")
        if page == "company":
            st.markdown("""
**This page — Company overview (demo).** A fictional 26-person company. Drill **total → department →
team → person** to answer three questions every finance team has:
- **Who's draining spend?** Biggest spenders on tools they actually use — plus **AI-token drains**
  (people burning far above the team median).
- **Who's paying but NOT using?** Idle seats & token budgets with no usage — **reclaim these first**.
- **Where are the gaps?** Spend with no owner, or a paid seat with zero usage.
Each finding rolls up into ranked **action items** (owner · channel · approval gate). Numbers are
deterministic — no LLM invents them.
""")
        else:
            st.markdown(f"""
**This page — Connect your data.** Bring your **own** usage/billing export and the full pipeline runs
on it. Where your data lives:
- **Usage / consumption** → cloud & AI billing exports (AWS Cost Explorer, OpenAI/Anthropic usage) or
  your **warehouse** (Snowflake/BigQuery). Export to **CSV** and upload — we never ask for DB credentials.
- **Invoices / cost** → include a `cost` column and rated spend reproduces your real bill.
- **Contract terms / commitments** → usually PDF contracts (extraction is on the roadmap).
- **Outcome KPIs** → your BI/warehouse. What you don't supply simply doesn't render — nothing is faked.

**How this page is organized** (FinOps framework — visibility → optimization → alignment):
1. **① Visibility:** total spend & trend, spend by vendor / team, renewals, uncommitted (shadow) spend.
2. **② Optimization:** waste, commitment utilization, budget vs forecast, cost-spike alerts, billing variance.
3. **③ Business alignment:** cost per outcome (unit economics).

**🤖 LLM advisor (`{cfg.advisor_model}`)** turns metrics into findings + actions; a grounding critic
rejects any figure not already on this page — it **cannot invent a number**, and it only ever sees
aggregated totals, never your raw rows. Base currency: {cfg.base_currency}.
""")


st.sidebar.markdown("### Data source")
source = st.sidebar.radio("Where does the data come from?",
                          ["Company overview (demo)", "Connect a warehouse (usage) — demo"])
try:
    if source.startswith("Company overview"):
        render_guide("company")
        import company as co
        al = co.synth_company()
        total = sum(a.monthly_cost for a in al)
        n_people = len({a.person for a in al})
        st.markdown("## 🏢 Northwind — company spend control tower")
        st.caption("Fictional 26-person company. Drill total → department → team → person to find who's "
                   "draining spend, who's paying for seats/tokens they don't use, and where the gaps are.")

        dead = co.dead_spend(al)
        ta = co.token_analysis(al)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total spend / mo", f"${total:,.0f}")
        k2.metric("People", n_people)
        k3.metric("Reclaimable now", f"${dead['reclaimable_monthly']:,.0f}", f"{dead['count']} idle seats")
        k4.metric("Token drains", len(ta["drains"]), "review needed" if ta["drains"] else "clean")

        st.markdown("### 1 · Where the money goes (drill down)")
        lvl = st.radio("Break spend down by", ["department", "team", "person", "tool", "category"],
                       horizontal=True)
        _df = pd.DataFrame(co.spend_by(al, lvl))
        c1, c2 = st.columns([2, 3])
        c1.dataframe(_df, hide_index=True, use_container_width=True,
                     column_config={"cost": st.column_config.NumberColumn(format="$%d")})
        c2.bar_chart(_df.set_index("key")["cost"])

        st.markdown("### 2 · Who's draining spend")
        st.caption("Biggest spenders on tools they actually **use** (idle spend is waste, shown below — not a drain).")
        st.dataframe(pd.DataFrame(co.top_drains(al, "person", 6)), hide_index=True, use_container_width=True,
                     column_config={"cost": st.column_config.NumberColumn(format="$%d")})
        if ta["drains"]:
            st.markdown("**🔥 AI-token drains** — burning far above the team median "
                        f"({ta['median_tokens']/1e6:.1f}M tokens):")
            st.dataframe(pd.DataFrame([{"Person": d["person"], "Team": d["team"],
                                        "Tokens/mo": f"{d['tokens']/1e6:.1f}M", "Cost": d["cost"],
                                        "× median": d["vs_median"]} for d in ta["drains"]]),
                         hide_index=True, use_container_width=True,
                         column_config={"Cost": st.column_config.NumberColumn(format="$%d")})

        st.markdown("### 3 · Paying but NOT using (dead spend)")
        st.caption(f"**${dead['reclaimable_monthly']:,.0f}/mo** in seats & budgets with no usage — reclaim these first.")
        st.dataframe(pd.DataFrame(dead["items"]), hide_index=True, use_container_width=True,
                     column_config={"reclaimable": st.column_config.NumberColumn(format="$%d")})
        if ta["unused_budget"]:
            st.caption("Plus unused AI budgets: " +
                       ", ".join(f"{u['person']} ({u['team']})" for u in ta["unused_budget"]))

        st.markdown("### 4 · Gaps")
        _gaps = co.gaps(al)
        st.dataframe(pd.DataFrame(_gaps), hide_index=True, use_container_width=True) if _gaps \
            else st.success("No structural gaps — every dollar has an owner and a usage signal.")

        st.markdown("### 5 · Smart suggestions & action items")
        for a in co.company_actions(al):
            tag = "🔒 needs approval" if a["requires_approval"] else "▶️ ready"
            with st.container(border=True):
                st.markdown(f"**{a['title']}**  · _{a['urgency']}_ · {tag}")
                st.caption(f"{a['detail']}  \n**Owner:** {a['owner']} · **Channel:** {a['channel']}")
        st.stop()

    # ── Connect your data (the only non-demo-company source) ─────────────────────
    render_guide("warehouse")
    st.markdown("## 🔌 Connect your data")

    with st.expander("🔒 Security & privacy — read before connecting (important)", expanded=True):
        st.markdown("""
- **We never ask for database credentials or run your SQL.** You export a CSV and upload it — nothing
  connects back into your systems, so there's no injection surface and no standing access to revoke.
- **Processed in memory, in your session only.** Your rows are **not stored, logged, or shared**, and
  they're gone the moment you refresh or close the tab.
- **No PII or secrets needed.** The pipeline only needs *vendor · period · unit_type · units · cost ·
  team/project*. Please **strip names, emails, and secrets** before uploading — pseudonymous team IDs
  are enough to find waste and drains.
- **The deterministic engine runs locally.** No third party sees your data. The optional **LLM advisor
  is off until you click it**, and even then it receives **only aggregated totals**, never raw rows.
- **Only upload data you're authorized to share.** You are responsible for your organization's policies.
- **Bad rows are rejected with a reason, never guessed** — the dashboard never shows a faked number.
""")

    mode = st.radio("How do you want to connect?",
                    ["Use demo data", "Upload a usage/billing CSV (recommended for your data)",
                     "Advanced: live SQL (self-hosted only)"], horizontal=False)

    report = {"rejected": [], "unpriced_vendors": [], "matched": 0, "vendors": 0}

    if mode.startswith("Upload"):
        tmpl = ("vendor,period,unit_type,units_consumed,cost,team,project,model\n"
                "Anthropic,2026-06,TOKEN,5100000,408.00,ml,assistant,claude-sonnet-5\n"
                "Snowflake,2026-06,GB,1200,240.00,data,warehouse,\n")
        st.download_button("⬇️ Download CSV template", tmpl, "usage_template.csv", "text/csv")
        st.caption(f"Required columns → **{', '.join(REQUIRED_FIELDS)}**. Optional → "
                   f"{', '.join(OPTIONAL_FIELDS)}. Column names can differ — you'll map them next.")
        up = st.file_uploader("Upload your usage/billing export (CSV)", type=["csv"])
        if up is None:
            st.info("⬆️ Upload a CSV to continue, or switch to **Use demo data** to see it working.")
            st.stop()
        raw = pd.read_csv(up, dtype=str).fillna("")
        st.caption("Detected columns: " + ", ".join(raw.columns))
        guess = _guess_mapping(list(raw.columns))
        mapping_json = st.text_area("Map your columns → our fields  (our_field: your_column)",
                                    json.dumps(guess, indent=2), height=230)
        mapping = FieldMapping(json.loads(mapping_json))
        mapped = mapping.apply(raw.to_dict("records"))
        ds, report = dataset_from_usage_rows(mapped, cfg)

    elif mode.startswith("Use demo"):
        mapped = FieldMapping(DEMO_MAPPING).apply(DEMO_ROWS)
        ds, report = dataset_from_usage_rows(mapped, cfg)
        st.caption("Demo uses a *different* source schema (supplier/month/meter/qty/amount) mapped to our "
                   "fields — the exact path your real CSV takes.")

    else:  # Advanced: live SQL
        st.warning("**Self-hosted only.** Live SQL is for a copy you run yourself with a **read-only** "
                   "connection supplied via environment variables — **never type production credentials "
                   "into a public instance**. On this hosted demo it runs an in-memory sample DB.")
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE usage(supplier TEXT, month TEXT, meter TEXT, qty REAL, amount REAL, team TEXT, project TEXT)")
        conn.executemany("INSERT INTO usage VALUES (?,?,?,?,?,?,?)",
                         [(r["supplier"], r["month"], r["meter"], float(r["qty"]), float(r["amount"]),
                           r["team"], r["project"]) for r in DEMO_ROWS])
        conn.commit()
        query = st.text_area("Read-only SQL (usage rows)", "SELECT * FROM usage", height=90)
        rows = SQLConnector(conn, query).fetch()
        mapped = FieldMapping({**DEMO_MAPPING, "cost": "amount"}).apply(rows)
        ds, report = dataset_from_usage_rows(mapped, cfg)

    st.success(f"✅ Matched **{report['matched']}** rows → **{report['vendors']}** vendors · "
               f"rejected **{len(report['rejected'])}**.")
    if report["rejected"]:
        st.warning(f"{len(report['rejected'])} rows rejected (not shown in the dashboard — never faked):")
        st.dataframe(pd.DataFrame([{"row": r["row"], "reason": r["reason"]} for r in report["rejected"]]),
                     hide_index=True, use_container_width=True)
    if report["unpriced_vendors"]:
        st.caption("⚠️ No `cost` column for: " + ", ".join(report["unpriced_vendors"]) +
                   " — these are shown **un-priced** (rate defaults to 1.0). Add a cost column for real spend.")
    if not ds.vendors:
        st.info("No valid rows to analyze yet — fix the mapping or the rejected rows above.")
        st.stop()

    rec = reconcile(ds, cfg)
    rated = rate_portfolio(ds, cfg)
    monthly = portfolio_monthly_cost(rated)
except json.JSONDecodeError as e:
    st.error(f"Field mapping is not valid JSON: {e}")
    st.stop()
except Exception as e:  # noqa: BLE001
    st.error(f"Pipeline failed: {e}")
    st.stop()

# ── shared metric computations ────────────────────────────────────────────────
bv = billing_variance(ds, rated, cfg)
waste = total_waste(rated)
mq = mom_qoq_cost(monthly)
budget = st.sidebar.number_input("Annual budget ($)", 50_000, 5_000_000, 200_000, 50_000)
breach = budget_breach(monthly, annual_budget=budget)
kpi_name = st.sidebar.text_input("Outcome KPI", "tickets_resolved")
this_month = monthly[-1][1] if monthly else 0.0

# ── ① VISIBILITY — where the money goes ───────────────────────────────────────
st.markdown("#### ① Visibility — where the money goes")
v1, v2, v3, v4 = st.columns(4)
v1.metric("Annualized run-rate", f"${breach['run_rate_monthly']*12:,.0f}")
v2.metric("This month", f"${this_month:,.0f}", delta=f"{mq['mom']*100:+.1f}% MoM", delta_color="off")
v3.metric("QoQ trend", f"{mq['qoq']*100:+.1f}%", delta_color="off")
v4.metric("Vendors", len(ds.vendors))
st.line_chart(pd.DataFrame(monthly, columns=["period", "cost"]).set_index("period"))
va, vb = st.columns(2)
with va:
    st.markdown("**Spend by vendor**")
    dfv = pd.DataFrame(spend_allocation(ds, rated, "vendor"))
    if not dfv.empty:
        st.bar_chart(dfv.set_index("key"))
with vb:
    st.markdown("**Spend by team** (who's spending)")
    st.dataframe(pd.DataFrame(spend_allocation(ds, rated, "team_id")), hide_index=True,
                 use_container_width=True, column_config={"cost": st.column_config.NumberColumn(format="$%d")})
rv, rx = st.columns(2)
with rv:
    st.markdown("**Upcoming renewals**")
    st.dataframe(pd.DataFrame(upcoming_renewals(ds, cfg)), hide_index=True, use_container_width=True)
with rx:
    st.markdown("**Uncommitted / shadow spend** (usage with no contract)")
    exc = rec["exceptions"]
    st.dataframe(pd.DataFrame(exc) if exc else pd.DataFrame([{"status": "all usage matched a commitment"}]),
                 hide_index=True, use_container_width=True)

# ── ② OPTIMIZATION — how efficiently it's spent ───────────────────────────────
st.divider()
st.markdown("#### ② Optimization — how efficiently it's spent")
o1, o2, o3 = st.columns(3)
o1.metric("Total waste", f"${waste['total_waste']:,.0f}", help="expiring prepaid + unused reserved")
o2.metric("Budget breach", ("⚠️ yes" if breach["breach"] else "✅ no"),
          delta=(f"in {breach['breach_in_months']} mo" if breach["breach_in_months"] else "within horizon"),
          delta_color="inverse" if breach["breach"] else "off")
o3.metric("Billing variance (Σ)", f"${sum(x['variance'] for x in bv):,.0f}",
          help="invoiced − rated expected; catches over-billing / errors")
oa, ob = st.columns(2)
with oa:
    st.markdown("**Commitment utilization** (prepaid runway / reserved use)")
    st.dataframe(pd.DataFrame([{"line": u["line_id"], "prepaid remaining": u["prepaid_remaining"],
                                "runway (mo)": u["prepaid_runway_months"]} for u in commitment_utilization(rated)]),
                 hide_index=True, use_container_width=True)
with ob:
    st.markdown("**Billing variance** (invoiced vs rated — dispute the gaps)")
    st.dataframe(pd.DataFrame(bv), hide_index=True, use_container_width=True, column_config={
        "invoiced": st.column_config.NumberColumn(format="$%d"),
        "expected": st.column_config.NumberColumn(format="$%d"),
        "variance": st.column_config.NumberColumn(format="$%d")})

st.markdown("**📆 Cost-spike alerts — WoW / MoM / QoQ** (a spike escalates immediately)")
recs = recommendations(ds, cfg)
rec_table = pd.DataFrame([{
    "": URGENCY_ICON.get(r["urgency"], "⚪"),
    "Vendor": r["vendor"],
    "Owner": r["owner"],
    "Urgency": r["urgency"],
    "WoW": None if r["wow"] is None else r["wow"] * 100,
    "MoM": None if r["mom"] is None else r["mom"] * 100,
    "QoQ": None if r["qoq"] is None else r["qoq"] * 100,
    "Weekly cost": r["current_weekly_cost"],
    "Recommendation": r["recommendation"],
    "Action": r["action_link"] or None,
} for r in recs])
st.dataframe(rec_table, hide_index=True, use_container_width=True, column_config={
    "WoW": st.column_config.NumberColumn(format="%.1f%%"),
    "MoM": st.column_config.NumberColumn(format="%.1f%%"),
    "QoQ": st.column_config.NumberColumn(format="%.1f%%"),
    "Weekly cost": st.column_config.NumberColumn(format="$%d"),
    "Action": st.column_config.LinkColumn("Action", display_text="📅 review"),
})
for r in recs:
    if r["urgency"] == "URGENT":
        st.error(f"🚨 {r['vendor']} — {r['recommendation']}  ·  **{r['action_text']}**")

# ── ③ BUSINESS ALIGNMENT — does spend produce value ───────────────────────────
st.divider()
st.markdown("#### ③ Business alignment — does spend produce value")
st.dataframe(pd.DataFrame(cost_per_outcome(ds, rated, kpi_name)), hide_index=True, use_container_width=True,
             column_config={"cost": st.column_config.NumberColumn(format="$%d"),
                            "cost_per_outcome": st.column_config.NumberColumn(format="$%.2f")})
st.caption("Cost attributed to a project ÷ its business KPI. Cost with no KPI = un-measurable ROI (gated).")

st.divider()
st.markdown(f"**🤖 LLM advisor** — `{cfg.advisor_model}` (grounded: every finding must cite an exact metric)")
byok = st.text_input("Your Anthropic API key (bring-your-own — never stored, used only for this call)",
                     type="password", placeholder="sk-ant-…")
st.caption("🔑 The advisor runs on **your** key so a public demo never spends the host's credits. "
           "It only ever sees the aggregated numbers above — never your raw rows.")
if st.button("Run advisor (live call)", disabled=not byok):
    with st.spinner(f"Asking {cfg.advisor_model}…"):
        try:
            from dataclasses import replace
            ctx = build_context(bv, waste, breach, cost_per_outcome(ds, rated, kpi_name), monthly)
            res = advise(ctx, replace(cfg, api_key=byok))
            st.success(f"{len(res['valid'])} grounded finding(s) · {len(res['rejected'])} rejected (ungrounded)")
            for f in res["valid"]:
                st.markdown(f"**• {f.get('claim')}**")
                st.caption(f"cites {f.get('cites_metric')} = {f.get('cited_value')} · "
                           f"action: {f.get('recommended_action')} · est. savings: ${f.get('est_savings', 0):,.0f}")
            for r in res["rejected"]:
                st.warning(f"✗ rejected (ungrounded): cited {r.get('cited_value')} — {r.get('claim')}")
        except Exception as e:  # noqa: BLE001
            st.error(f"Advisor call failed: {e}")
else:
    st.caption("Click to make a live call; findings are validated by the deterministic grounding critic before display.")

# ── ⚡ AUTOMATE THE FINDINGS — minimal manual work ─────────────────────────────
st.divider()
st.markdown("#### ⚡ Automate the findings — close the loop with minimal manual work")
st.caption("Every finding becomes an action. **Low-risk actions auto-fire** (Slack alert · ticket · "
           "calendar prep); **money-moving actions** (invoice disputes, renegotiations) wait for your "
           "one-click approval. In production the sinks inject your real Slack / Jira / email clients.")

_ICON = {"URGENT": "🚨", "ELEVATED": "🟠", "STRATEGIC": "🔵", "OK": "🟢"}
acts = derive_actions(ds, rated, cfg, annual_budget=budget, kpi_name=kpi_name)
st.dataframe(pd.DataFrame([{
    "": _ICON.get(a.urgency, "⚪"), "Action": a.title, "Source": a.source, "Owner": a.owner,
    "Channel": a.channel, "$ impact": a.amount, "Approval": "✋ needed" if a.requires_approval else "auto",
} for a in acts]), hide_index=True, use_container_width=True,
    column_config={"$ impact": st.column_config.NumberColumn(format="$%d")})

need = [a for a in acts if a.requires_approval]
approved_ids = set()
if need:
    picks = st.multiselect("Approve money-moving actions to fire:", [a.title for a in need])
    approved_ids = {a.id for a in need if a.title in picks}


class _DemoSink:   # stand-in for a real Slack/Jira/email client in the demo
    def __init__(self, ch): self.ch = ch
    def send(self, action): return {"channel": self.ch, "ok": True, "note": "demo — inject a real client in prod"}


sinks = {"slack": _DemoSink("slack"), "ticket": _DemoSink("ticket"),
         "email": _DemoSink("email"), "calendar": CalendarSink()}
n_auto = sum(1 for a in acts if not a.requires_approval)
if st.button(f"⚡ Run automation  ({n_auto} auto-fire · {len(need)} awaiting approval)"):
    log = dispatch(acts, sinks, approved=approved_ids, dry_run=False)
    st.success(f"Fired {len(log['sent'])} · still awaiting approval {len(log['queued_for_approval'])} · errors {len(log['errors'])}")
    for s in log["sent"]:
        link = f"  ·  [📅 open]({s['link']})" if s.get("link") else ""
        st.write(f"✅ **{s['id']}** → {s['channel']}{link}")
    for q in log["queued_for_approval"]:
        st.write(f"✋ awaiting approval: {q['title']}  (${q['amount']:,.0f})")
else:
    st.caption("Preview above. Click to fire the auto-actions now; approved money-moving actions fire too.")
