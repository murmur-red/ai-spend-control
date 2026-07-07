"""Delivery sinks for actions — pluggable, dependency-injected so delivery is testable without creds.

Each sink takes an injected client (Slack WebClient, a Jira/Linear client, an email client) and
`send(action)`s. CalendarSink needs no client (it returns a pre-filled event link). `dispatch()`
routes each action to its channel's sink, auto-firing low-risk actions and queuing money-moving ones
until approved.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class ActionSink(Protocol):
    def send(self, action) -> dict[str, Any]:
        ...


class SlackSink:
    def __init__(self, client: Any, channel: str = "#finops"):
        self._c, self._ch = client, channel

    def send(self, action) -> dict[str, Any]:
        self._c.chat_postMessage(channel=self._ch,
                                 text=f"[{action.urgency}] {action.title} — {action.detail} (owner: {action.owner})")
        return {"channel": "slack", "ok": True, "to": self._ch}


class TicketSink:
    def __init__(self, client: Any):
        self._c = client

    def send(self, action) -> dict[str, Any]:
        key = self._c.create_issue(summary=action.title, description=action.detail, assignee=action.owner)
        return {"channel": "ticket", "ok": True, "id": key}


class EmailSink:
    def __init__(self, client: Any):
        self._c = client

    def send(self, action) -> dict[str, Any]:
        self._c.send(to=action.owner, subject=action.title, body=action.detail)
        return {"channel": "email", "ok": True, "to": action.owner}


class CalendarSink:
    def send(self, action) -> dict[str, Any]:
        link = action.payload.get("link") or (
            "https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={urllib.parse.quote(action.title)}"
            f"&details={urllib.parse.quote(action.detail + chr(10) + 'Owner: ' + action.owner)}")
        return {"channel": "calendar", "ok": True, "link": link}


def dispatch(actions, sinks: dict[str, ActionSink], approved: Optional[set[str]] = None,
             dry_run: bool = True) -> dict[str, list]:
    """Route actions to their channel sink. Money-moving actions (requires_approval) are held until
    their id is in `approved`. dry_run plans without firing. Returns a delivery log."""
    approved = approved or set()
    sent, queued, planned, errors = [], [], [], []
    for a in actions:
        if a.requires_approval and a.id not in approved:
            queued.append({"id": a.id, "title": a.title, "channel": a.channel, "amount": a.amount})
            continue
        if dry_run:
            planned.append({"id": a.id, "title": a.title, "channel": a.channel})
            continue
        sink = sinks.get(a.channel)
        if sink is None:
            errors.append({"id": a.id, "reason": f"no sink for channel '{a.channel}'"})
            continue
        try:
            sent.append({"id": a.id, **sink.send(a)})
        except Exception as e:  # noqa: BLE001
            errors.append({"id": a.id, "reason": str(e)})
    return {"sent": sent, "queued_for_approval": queued, "planned": planned, "errors": errors}
