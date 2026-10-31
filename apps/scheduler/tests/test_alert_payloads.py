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
    assert embed["title"] == payloads.discord_text(OPENED.summary)
    assert embed["url"] == OPENED.link
    assert embed["color"] == 0xD92D20


def test_teams_receives_an_adaptive_card() -> None:
    body = json.loads(payloads.teams(OPENED))
    [attachment] = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["body"][0]["text"] == payloads.teams_text(OPENED.summary)
    assert card["body"][0]["color"] == "Attention"
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "Open in nexovue", "url": OPENED.link}
    ]


def test_pagerduty_triggers_with_the_subject_as_its_dedup_key() -> None:
    """AC20."""
    body = json.loads(
        payloads.pagerduty(
            OPENED,
            routing_key="k" * 32,
            dedup_key="incident:1",
            subject="Checkout API",
            app_base_url="https://app.nexovue.test",
        )
    )
    assert body["event_action"] == "trigger"
    assert body["dedup_key"] == "incident:1"
    assert "note" not in body["payload"]["custom_details"]
    assert body["payload"]["severity"] == "critical"
    assert body["payload"]["summary"] == OPENED.summary
    assert body["payload"]["source"] == "nexovue"
    assert body["links"] == [{"href": OPENED.link, "text": "Open in nexovue"}]


def test_pagerduty_resolves_the_same_key_and_says_nothing_else() -> None:
    body = json.loads(
        payloads.pagerduty(
            RESOLVED,
            routing_key="k" * 32,
            dedup_key="incident:1",
            subject="Checkout API",
            app_base_url="https://app.nexovue.test",
        )
    )
    assert body == {"routing_key": "k" * 32, "event_action": "resolve", "dedup_key": "incident:1"}


def test_the_webhook_body_is_version_2_and_names_its_subject() -> None:
    """ADR-022: `data.subject` names an incident or a finding, and `type` leads with
    which (docs/product/setup/alerts/webhooks.md)."""
    opened_at = datetime(2026, 9, 19, 14, 3, tzinfo=UTC)
    body = json.loads(
        payloads.webhook(
            RESOLVED,
            subject_kind="incident",
            subject_id="incident-1",
            subject="Checkout API",
            opened_at=opened_at,
            resolved_at=opened_at + timedelta(minutes=12),
            sent_at=opened_at + timedelta(minutes=12),
        )
    )
    assert body["version"] == 2
    assert body["type"] == "incident.resolved"
    assert body["data"]["subject"] == {
        "kind": "incident",
        "id": "incident-1",
        "label": "Checkout API",
        "severity": "critical",
        "opened_at": "2026-09-19T14:03:00+00:00",
        "resolved_at": "2026-09-19T14:15:00+00:00",
    }
    assert "incident" not in body["data"]
    assert body["data"]["rule"] == {"name": "Any check down"}
    assert body["data"]["link"] == OPENED.link


def test_a_finding_s_webhook_is_a_finding_event() -> None:
    joined = dataclasses.replace(OPENED, kind="joined", rule_name="")
    body = json.loads(
        payloads.webhook(
            joined,
            subject_kind="finding",
            subject_id="finding-1",
            subject="checkout",
            opened_at=datetime(2026, 9, 19, 14, 3, tzinfo=UTC),
            resolved_at=None,
            sent_at=datetime(2026, 9, 19, 14, 9, tzinfo=UTC),
        )
    )
    assert body["type"] == "finding.joined"
    assert body["data"]["subject"]["kind"] == "finding"
    # A finding told outside any rule, as adoption is, names none.
    assert body["data"]["rule"] is None


def test_a_finding_noted_on_a_linked_incident_adds_to_its_alert_and_says_why() -> None:
    """D29, AC45: a trigger with the open alert's key is added to that alert's log;
    PagerDuty opens no second incident."""
    body = json.loads(
        payloads.pagerduty(
            OPENED,
            routing_key="k" * 32,
            dedup_key="incident:1",
            subject="checkout",
            app_base_url="https://app.nexovue.test",
            noted=True,
        )
    )
    assert (body["event_action"], body["dedup_key"]) == ("trigger", "incident:1")
    assert body["payload"]["custom_details"]["note"] == payloads.PAGERDUTY_NOTE


def test_an_incident_closed_unrecovered_resolves_pagerduty_all_the_same() -> None:
    """Its rule or check was removed: PagerDuty must stop paging for it."""
    closed = dataclasses.replace(RESOLVED, kind="closed", summary="[closed] Checkout API")
    body = json.loads(
        payloads.pagerduty(
            closed,
            routing_key="k" * 32,
            dedup_key="incident:1",
            subject="Checkout API",
            app_base_url="https://app.nexovue.test",
        )
    )
    assert body == {"routing_key": "k" * 32, "event_action": "resolve", "dedup_key": "incident:1"}


# A subject's name comes from telemetry, which anyone holding a public browser key can
# send: it must arrive in a chat as the words it is, never as the chat's own markup.
HOSTILE = dataclasses.replace(
    OPENED,
    summary=(
        "[critical] <!channel> <https://evil.example|Reset your password> & "
        "[x](https://evil.example) is down"
    ),
    details=("<!here> *bold* [Reset](https://evil.example) is down.",),
)


def test_slack_shows_telemetry_text_literally_and_keeps_its_own_link() -> None:
    body = json.loads(payloads.slack(HOSTILE))
    assert "<!channel>" not in body["text"]
    assert "&lt;!channel&gt; &lt;https://evil.example|Reset your password&gt; &amp;" in body["text"]
    [attachment] = body["attachments"]
    assert "<!here>" not in attachment["text"]
    assert "&lt;!here&gt;" in attachment["text"]
    assert attachment["fallback"] == body["text"]
    assert attachment["text"].endswith(f"\n<{OPENED.link}|Open in nexovue>")


def test_teams_shows_telemetry_text_without_a_link_in_it() -> None:
    card = json.loads(payloads.teams(HOSTILE))["attachments"][0]["content"]
    texts = [block["text"] for block in card["body"]]
    assert all("[x](" not in text and "[Reset](" not in text for text in texts)
    assert "\\[Reset\\](https://evil.example)" in texts[1]
    assert card["actions"][0]["url"] == OPENED.link


def test_discord_shows_telemetry_text_without_markdown() -> None:
    [embed] = json.loads(payloads.discord(HOSTILE))["embeds"]
    assert "[Reset](" not in embed["description"]
    assert "\\[Reset\\]\\(https://evil.example\\)" in embed["description"]
    assert "\\*bold\\*" in embed["description"]
    assert "\\[x\\]" in embed["title"]
    assert embed["url"] == OPENED.link
    assert embed["description"].endswith(f"Open in nexovue: {OPENED.link}")
