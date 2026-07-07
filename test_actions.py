#!/usr/bin/env python3
"""
Tests for the action-automation layer: findings → actions → dispatch (approval gate + injected sinks).
Run: python test_actions.py
"""
from __future__ import annotations

import sys

from config import SpendConfig
from adapter import load_dataset
from reconcile import reconcile
from metrics import rate_portfolio
from actions import derive_actions
from action_sinks import SlackSink, TicketSink, EmailSink, CalendarSink, ActionSink, dispatch

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


class FakeSlack:
    def __init__(self): self.sent = []
    def chat_postMessage(self, channel, text): self.sent.append((channel, text))


class FakeTracker:
    def __init__(self): self.issues = []
    def create_issue(self, summary, description, assignee): self.issues.append(summary); return f"TICK-{len(self.issues)}"


class FakeMail:
    def __init__(self): self.sent = []
    def send(self, to, subject, body): self.sent.append((to, subject))


def main() -> None:
    cfg = SpendConfig()
    ds = load_dataset(cfg)["dataset"]
    reconcile(ds, cfg)
    rated = rate_portfolio(ds, cfg)

    actions = derive_actions(ds, rated, cfg, annual_budget=200_000)
    by_src = {a.source for a in actions}
    check("billing dispute action derived", "billing_variance" in by_src)
    check("budget renegotiation derived", "budget" in by_src)
    check("cost-spike action derived", "cost_spike" in by_src)
    dispute = next(a for a in actions if a.source == "billing_variance")
    check("dispute requires approval (money-moving)", dispute.requires_approval is True)
    check("dispute carries $ impact", dispute.amount >= 1000)
    spike = next(a for a in actions if a.source == "cost_spike")
    check("cost spike auto-fires (no approval)", spike.requires_approval is False)

    # ── Dispatch: dry-run holds money-moving for approval, plans the rest ────
    slack, tracker, mail = FakeSlack(), FakeTracker(), FakeMail()
    sinks = {"slack": SlackSink(slack), "ticket": TicketSink(tracker), "email": EmailSink(mail), "calendar": CalendarSink()}
    for s in (sinks["slack"], sinks["ticket"], sinks["email"], sinks["calendar"]):
        check(f"{type(s).__name__} is an ActionSink", isinstance(s, ActionSink))

    plan = dispatch(actions, sinks, dry_run=True)
    check("approval-required actions queued in dry-run", len(plan["queued_for_approval"]) >= 2)
    check("auto actions planned (nothing fired in dry-run)", len(plan["planned"]) >= 1 and not plan["sent"])
    check("dry-run sends nothing to sinks", not slack.sent and not tracker.issues and not mail.sent)

    # ── Live dispatch with everything approved → sinks actually fire ────────
    approve = {a.id for a in actions}
    log = dispatch(actions, sinks, approved=approve, dry_run=False)
    check("all actions sent when approved", len(log["sent"]) == len(actions) and not log["errors"])
    check("ticket sink created an issue (dispute)", len(tracker.issues) >= 1)
    check("calendar sink returns a link", any(s.get("link", "").startswith("https://calendar") for s in log["sent"]))

    # ── Missing sink is an error, not a crash ───────────────────────────────
    err = dispatch(actions, {}, approved=approve, dry_run=False)
    check("missing sink reported as error", len(err["errors"]) >= 1 and not err["sent"])

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
