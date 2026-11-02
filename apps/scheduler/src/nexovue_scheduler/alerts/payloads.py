"""Each channel's native format (FR17), built from one `AlertMessage`.

Pure: what goes over the wire is decided here and sent by `senders.http`, so every
format can be read and tested without a receiver. Nothing is added that the
message does not already carry, so BR9 holds here because it holds there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from nexovue_platform.alerts.model import ENDING_KINDS

from nexovue_scheduler.alerts.messages import AlertMessage

# Chat colours by what happened. Not design tokens: they are drawn by Slack and
# Discord, on their own backgrounds, and read as the conventional traffic lights.
_RED: Final = "#d92d20"
_AMBER: Final = "#f79009"
_GREEN: Final = "#12b76a"

# The version of the webhook body's schema (FR4). A change a receiver would have
# to handle is a new version, never an edit to this one. Version 2 added findings
# and moved the incident under `data.subject` (ADR-022); version 1 is not kept,
# because nexovue had no receiver of it yet.
WEBHOOK_SCHEMA_VERSION: Final = 2

PAGERDUTY_SUMMARY_LIMIT: Final = 1024
PAGERDUTY_SOURCE: Final = "nexovue"
# D29, D35: said on the alert a linked finding or incident opened first, so the
# responder knows the trigger log entry is the same problem, not a second one.
PAGERDUTY_NOTE: Final = (
    "nexovue found this on the same service as the alert already open here, so it is "
    "added to that alert rather than paging again."
)


def slack_text(text: str) -> str:
    """Slack's three control characters, `&` first so its own entities stay intact.
    Slack decodes only these, so a name shows as written and can never become a
    mention or a link (api.slack.com/docs/formatting, *Escaping text*)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def teams_text(text: str) -> str:
    """Brackets escaped, so `[words](url)` in a name is never a link. Only brackets:
    Teams mobile shows other escapes as stray backslashes. Unverified against
    Microsoft's documentation; taken from Review Board's tested change (r/13892)."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


_DISCORD_MARKDOWN: Final = re.compile(r"([\\*_~`|>\[\]()#-])")


def discord_text(text: str) -> str:
    """Discord's markdown characters escaped. An embed renders `[words](url)` as a
    disguised link, which a name must never become. Unverified against Discord's
    documentation, which does not cover embed escaping."""
    return _DISCORD_MARKDOWN.sub(r"\\\1", text)


def colour(message: AlertMessage) -> str:
    if message.kind == "resolved":
        return _GREEN
    return _RED if message.severity == "critical" else _AMBER


def _lines_with_link(message: AlertMessage, escape: Callable[[str], str]) -> str:
    return "\n".join((*map(escape, message.details), f"Open in nexovue: {message.link}"))


def slack(message: AlertMessage) -> bytes:
    """An incoming-webhook message: the summary as text, so a notification preview
    reads well, and the detail in a coloured attachment."""
    summary = slack_text(message.summary)
    body = {
        "text": summary,
        "attachments": [
            {
                "color": colour(message),
                "fallback": summary,
                "text": "\n".join(map(slack_text, message.details))
                + f"\n<{message.link}|Open in nexovue>",
            }
        ],
    }
    return _json(body)


def discord(message: AlertMessage) -> bytes:
    body = {
        "embeds": [
            {
                "title": discord_text(message.summary)[:256],
                "description": _lines_with_link(message, discord_text)[:4096],
                "url": message.link,
                "color": int(colour(message).removeprefix("#"), 16),
            }
        ],
        # Never @everyone, however a check or rule happens to be named.
        "allowed_mentions": {"parse": []},
    }
    return _json(body)


def teams(message: AlertMessage) -> bytes:
    """An Adaptive Card, which is what a Teams Workflows webhook posts."""
    accent = {"#d92d20": "Attention", "#f79009": "Warning", "#12b76a": "Good"}[colour(message)]
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": teams_text(message.summary),
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
                "color": accent,
            },
            *(
                {"type": "TextBlock", "text": teams_text(line), "wrap": True}
                for line in message.details
            ),
        ],
        "actions": [{"type": "Action.OpenUrl", "title": "Open in nexovue", "url": message.link}],
    }
    body = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }
    return _json(body)


def pagerduty(
    message: AlertMessage,
    *,
    routing_key: str,
    dedup_key: str,
    subject: str,
    app_base_url: str,
    noted: bool = False,
) -> bytes:
    """Events API v2. The subject's key is the `dedup_key`, so escalation updates the
    alert PagerDuty already has and recovery resolves exactly it (AC20). A subject
    closed without recovering resolves it too: nothing will ever send its recovery.

    `noted` (D29, D35): the key is a linked finding's or incident's, whose alert is
    open. PagerDuty adds a trigger with an open alert's key to that alert's log
    instead of opening a new one, which is the Events API's only way to add to an
    incident (developer.pagerduty.com, *Events API v2*, *Trigger Events*)."""
    if message.kind in ENDING_KINDS:
        return _json(
            {"routing_key": routing_key, "event_action": "resolve", "dedup_key": dedup_key}
        )
    details: dict[str, Any] = {"details": list(message.details)}
    if noted:
        details["note"] = PAGERDUTY_NOTE
    return _json(
        {
            "routing_key": routing_key,
            "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": {
                "summary": message.summary[:PAGERDUTY_SUMMARY_LIMIT],
                "source": PAGERDUTY_SOURCE,
                "severity": message.severity,
                "component": subject,
                "group": message.rule_name,
                "custom_details": details,
            },
            "links": [{"href": message.link, "text": "Open in nexovue"}],
            "client": "nexovue",
            "client_url": app_base_url or message.link,
        }
    )


def webhook(
    message: AlertMessage,
    *,
    subject_kind: str,
    subject_id: str,
    subject: str,
    opened_at: datetime,
    resolved_at: datetime | None,
    sent_at: datetime,
) -> bytes:
    """A documented, versioned JSON body (FR4); its schema is in
    `docs/product/setup/alerts/webhooks.md`. `type` is the subject's kind and what
    happened: `incident.opened`, `finding.joined`. `data.subject.label`, `summary` and
    `details` can carry a service or endpoint name from telemetry, which anyone with
    a browser key can send: the documentation says to treat them as untrusted text."""
    return _json(
        {
            "version": WEBHOOK_SCHEMA_VERSION,
            "type": f"{subject_kind}.{message.kind}",
            "timestamp": sent_at.astimezone(UTC).isoformat(),
            "data": {
                "subject": {
                    "kind": subject_kind,
                    "id": subject_id,
                    "label": subject,
                    "severity": message.severity,
                    "opened_at": opened_at.astimezone(UTC).isoformat(),
                    "resolved_at": resolved_at.astimezone(UTC).isoformat() if resolved_at else None,
                },
                "rule": {"name": message.rule_name} if message.rule_name else None,
                "summary": message.summary,
                "details": list(message.details),
                "link": message.link,
            },
        }
    )


def _json(body: dict[str, Any]) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
