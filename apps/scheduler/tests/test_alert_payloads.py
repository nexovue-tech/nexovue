"""Each channel's native format (FR17), read without a receiver."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

from nexovue_scheduler.alerts import payloads
from nexovue_scheduler.alerts.messages import AlertMessage

OPENED = AlertMessage(
    kind="opened",
    severity="critical",
    summary="[critical] Checkout API is down",
    details=("Checkout API is down.", "Why: expected status code less than 400, got 503."),
    link="https://app.nexovue.test/synthetics/c1",
    rule_name="Any check down",
    manage_link=None,
)
RESOLVED = AlertMessage(
    kind="resolved",
    severity="critical",
    summary="[resolved] Checkout API recovered",
    details=("Checkout API is up again.",),
    link=OPENED.link,
    rule_name=OPENED.rule_name,
    manage_link=None,
)


def test_slack_carries_the_summary_and_a_coloured_attachment_with_the_link() -> None:
    body = json.loads(payloads.slack(OPENED))
    assert body["text"] == OPENED.summary
    [attachment] = body["attachments"]
    assert attachment["color"] == "#d92d20"
    assert f"<{OPENED.link}|Open in nexovue>" in attachment["text"]


def test_a_warning_is_amber_and_a_recovery_green() -> None:
    warning = dataclasses.replace(OPENED, severity="warning")
    assert payloads.colour(warning) == "#f79009"
    assert payloads.colour(RESOLVED) == "#12b76a"


def test_discord_never_mentions_anyone() -> None:
    body = json.loads(payloads.discord(OPENED))
    assert body["allowed_mentions"] == {"parse": []}
    [embed] = body["embeds"]
    assert embed["title"] == OPENED.summary
    assert embed["url"] == OPENED.link
    assert embed["color"] == 0xD92D20


def test_teams_receives_an_adaptive_card() -> None:
    body = json.loads(payloads.teams(OPENED))
    [attachment] = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["body"][0]["text"] == OPENED.summary
    assert card["body"][0]["color"] == "Attention"
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "Open in nexovue", "url": OPENED.link}
    ]


def test_pagerduty_triggers_with_the_incident_as_its_dedup_key() -> None:
    """AC20."""
    body = json.loads(
        payloads.pagerduty(
            OPENED,
            routing_key="k" * 32,
            dedup_key="incident-1",
            subject="Checkout API",
            app_base_url="https://app.nexovue.test",
        )
    )
    assert body["event_action"] == "trigger"
    assert body["dedup_key"] == "incident-1"
    assert body["payload"]["severity"] == "critical"
    assert body["payload"]["summary"] == OPENED.summary
    assert body["payload"]["source"] == "nexovue"
    assert body["links"] == [{"href": OPENED.link, "text": "Open in nexovue"}]


def test_pagerduty_resolves_the_same_key_and_says_nothing_else() -> None:
    body = json.loads(
        payloads.pagerduty(
            RESOLVED,
            routing_key="k" * 32,
            dedup_key="incident-1",
            subject="Checkout API",
            app_base_url="https://app.nexovue.test",
        )
    )
    assert body == {"routing_key": "k" * 32, "event_action": "resolve", "dedup_key": "incident-1"}


def test_the_webhook_body_is_versioned_and_says_what_happened() -> None:
    opened_at = datetime(2026, 9, 19, 14, 3, tzinfo=UTC)
    body = json.loads(
        payloads.webhook(
            RESOLVED,
            incident_id="incident-1",
            subject="Checkout API",
            opened_at=opened_at,
            resolved_at=opened_at + timedelta(minutes=12),
            sent_at=opened_at + timedelta(minutes=12),
        )
    )
    assert body["version"] == 1
    assert body["type"] == "incident.resolved"
    assert body["data"]["incident"] == {
        "id": "incident-1",
        "subject": "Checkout API",
        "severity": "critical",
        "opened_at": "2026-09-19T14:03:00+00:00",
        "resolved_at": "2026-09-19T14:15:00+00:00",
    }
    assert body["data"]["link"] == OPENED.link


def test_an_incident_closed_unrecovered_resolves_pagerduty_all_the_same() -> None:
    """Its rule or check was removed: PagerDuty must stop paging for it."""
    closed = dataclasses.replace(RESOLVED, kind="closed", summary="[closed] Checkout API")
    body = json.loads(
        payloads.pagerduty(
            closed,
            routing_key="k" * 32,
            dedup_key="incident-1",
            subject="Checkout API",
            app_base_url="https://app.nexovue.test",
        )
    )
    assert body == {"routing_key": "k" * 32, "event_action": "resolve", "dedup_key": "incident-1"}
