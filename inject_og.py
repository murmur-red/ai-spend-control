"""Inject Open Graph / Twitter-card meta into Streamlit's static index.html at build time.

Streamlit serves a static HTML shell that social scrapers (Slack/X/LinkedIn) read *without* running
JS, so `st.set_page_config` can't provide a link preview. This patches the shell's <head> once during
the Docker build. Idempotent and defensive: it no-ops if the tags already exist or <head> is missing,
so a future Streamlit change can never break the build.
"""
from __future__ import annotations

import os

import streamlit

TAGS = (
    '<meta property="og:type" content="website">'
    '<meta property="og:title" content="AI Spend Control · murmur.red">'
    '<meta property="og:description" content="Turn SaaS + AI consumption into cost, budget, and value '
    '— find waste, drains, and dead spend before the invoice does.">'
    '<meta property="og:image" content="https://murmur.red/og-spend.png">'
    '<meta property="og:url" content="https://spend.murmur.red">'
    '<meta name="twitter:card" content="summary_large_image">'
    '<meta name="twitter:title" content="AI Spend Control · murmur.red">'
    '<meta name="twitter:description" content="Find waste, drains, and dead spend before the invoice does.">'
    '<meta name="twitter:image" content="https://murmur.red/og-spend.png">'
)


def inject(path: str | None = None) -> str:
    path = path or os.path.join(os.path.dirname(streamlit.__file__), "static", "index.html")
    html = open(path, encoding="utf-8").read()
    if "og:image" in html:
        return "already present"
    if "</head>" not in html:
        return "no </head> — skipped"
    open(path, "w", encoding="utf-8").write(html.replace("</head>", TAGS + "</head>", 1))
    return "injected"


if __name__ == "__main__":
    print("OG:", inject())
