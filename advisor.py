"""LLM advisor (piece 5) — the only LLM in either product.

Turns the computed metrics into plain-language findings + renegotiation/optimization actions, under
strict guardrails: temperature 0, structured-JSON output, EXPLAIN-ONLY (every finding must cite an
exact number that is present in the metrics), and a deterministic critic that rejects any finding
whose cited figure is not grounded in the input. The LLM call is isolated behind `llm=`, so the
context builder and critic are testable without any API call.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

SYSTEM = """\
You are a senior FinOps advisor. You are given a JSON of already-computed spend metrics for a company \
that BUYS SaaS + AI services. Explain ONLY what these numbers show, and recommend concrete \
renegotiation / optimization actions.

Rules:
- For every finding, cite the EXACT number from the input as `cited_value` — it MUST be a number that \
appears in the input JSON. Never invent, round, or estimate a figure that is not present.
- If the data is insufficient for a claim, do not make it.
- Output STRICT JSON only, no prose outside it:
  {"findings":[{"claim":str,"cites_metric":str,"cited_value":number,"recommended_action":str,"est_savings":number}]}
"""


def build_context(billing_variance: list, waste: dict, budget: dict,
                  cost_per_outcome: list, monthly_cost: list) -> dict[str, Any]:
    """Compact, numbers-only context for the advisor (no free text it could latch onto)."""
    return {
        "monthly_cost": [{"period": p, "cost": c} for p, c in monthly_cost],
        "billing_variance": billing_variance,
        "waste": waste,
        "budget": budget,
        "cost_per_outcome": cost_per_outcome,
    }


def allowed_values(context: Any) -> set[float]:
    """Every numeric value present anywhere in the context (the only figures the advisor may cite)."""
    out: set[float] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.add(round(float(x), 6))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(context)
    return out


def validate_findings(findings: list[dict], allowed: set[float], atol: float = 1e-6) -> dict[str, list]:
    """Critic: keep only findings whose cited_value EXACTLY matches a metric in the input; reject the
    rest. Uses only a tiny absolute tolerance for float-representation noise — no relative tolerance —
    so a value that is merely 'close' (e.g. 94009 vs 94000, or 0.9 vs 0.05) is rejected, enforcing the
    EXPLAIN-ONLY / cite-the-exact-number guardrail."""
    valid, rejected = [], []
    for f in findings:
        cv = f.get("cited_value")
        grounded = isinstance(cv, (int, float)) and not isinstance(cv, bool) and \
            any(abs(float(cv) - a) <= atol for a in allowed)
        if grounded:
            valid.append(f)
        else:
            rejected.append({**f, "reject_reason": "cited_value not grounded in the input metrics"})
    return {"valid": valid, "rejected": rejected}


def _default_llm(context: dict, config) -> list[dict]:
    """Live Claude call (temperature 0, JSON). Imported lazily so tests never need the SDK."""
    import anthropic

    key = getattr(config, "api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("No Anthropic API key — provide your own key to run the advisor.")
    client = anthropic.Anthropic(api_key=key)
    kwargs = dict(
        model=config.advisor_model,
        max_tokens=4000,   # ample headroom: newer models spend output tokens on thinking too
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Metrics:\n" + json.dumps(context) + "\n\nProduce the findings JSON."}],
    )
    try:
        # temperature 0 for determinism where supported; newer models deprecate it (grounding critic
        # is the real guardrail regardless).
        resp = client.messages.create(temperature=0, **kwargs)
    except anthropic.BadRequestError as e:
        if "temperature" in str(e).lower():
            resp = client.messages.create(**kwargs)
        else:
            raise
    # Newer models can return thinking blocks before the text; take the text block(s) only.
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text).get("findings", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def advise(context: dict, config, llm: Optional[Callable[[dict, Any], list]] = None) -> dict[str, Any]:
    """Generate findings via the LLM, then run the deterministic grounding critic over them."""
    findings = (llm or _default_llm)(context, config)
    result = validate_findings(findings, allowed_values(context))
    result["context"] = context
    return result
