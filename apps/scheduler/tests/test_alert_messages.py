"""The words of an alert (FR16), and what they never contain (BR9, AC24)."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

from nexovue_scheduler.alerts.messages import compose, compose_batch, describe_duration
from nexovue_scheduler.alerts.outbox import Notice

OPENED_AT = datetime(2026, 9, 19, 14, 3, tzinfo=UTC)
CHECK_ID = uuid.uuid4()
RULE_ID = uuid.uuid4()


def _notice(
    kind: str, *, severity: str = "critical", resolved: bool = False, **detail: object
) -> Notice:
    return Notice(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        incident_id=uuid.uuid4(),
        kind=kind,
        severity=severity,
        created_at=OPENED_AT,
        attempts=0,
        rule_id=RULE_ID,
        rule_name="Any check down",
        condition_kind="check_state",
        subject_key=f"check:{CHECK_ID}",
        subject_label="Checkout API",
        opened_at=OPENED_AT,
        resolved_at=OPENED_AT + timedelta(minutes=125) if resolved else None,
        detail={"check_id": str(CHECK_ID), "state": "down", "location": "local"} | detail,
    )


def test_an_opened_message_says_what_why_where_when_and_which_rule() -> None:
    message = compose(
        _notice("opened", reason="expected status code less than 400, got 503"),
        app_base_url="https://app.nexovue.test/",
    )
    assert message.summary == "[critical] Checkout API is down"
    assert message.details == (
        "Checkout API is down.",
        "Why: expected status code less than 400, got 503.",
        "Seen from: local.",
        "Started: 2026-09-19 14:03 UTC.",
        "Rule: Any check down.",
    )
    assert message.link == f"https://app.nexovue.test/synthetics/{CHECK_ID}"
    assert message.manage_link == f"https://app.nexovue.test/alerts/rules/{RULE_ID}"


def test_a_resolved_message_says_how_long_it_lasted() -> None:
    message = compose(_notice("resolved", resolved=True), app_base_url="https://app.nexovue.test")
    assert message.summary == "[resolved] Checkout API recovered"
    assert "Lasted: 2 h 5 min." in message.details


def test_an_escalation_says_it_got_worse() -> None:
    message = compose(_notice("escalated"), app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] Checkout API is now down"


def test_nothing_in_the_incident_beyond_the_named_facts_reaches_the_message() -> None:
    """AC24. The composer reads named keys only, so a field that should never have
    been stored still cannot travel."""
    leaked = "sk_live_do_not_leak"
    message = compose(
        _notice("opened", response_body=leaked, headers={"Authorization": leaked}),
        app_base_url="https://app.nexovue.test",
    )
    rendered = "\n".join((message.summary, *message.details, message.link))
    assert leaked not in rendered


def test_durations_are_coarse() -> None:
    start = OPENED_AT
    assert describe_duration(start, start + timedelta(seconds=20)) == "1 min"
    assert describe_duration(start, start + timedelta(minutes=45)) == "45 min"
    assert describe_duration(start, start + timedelta(hours=3)) == "3 h"


def test_several_checks_failing_at_once_read_as_one_outage() -> None:
    """FR13, AC13."""
    first = _notice("opened")
    second = dataclasses.replace(_notice("opened"), subject_label="Search API")
    message = compose_batch((first, second), app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] 2 checks are down"
    assert message.details == (
        "[critical] Checkout API is down",
        "[critical] Search API is down",
    )
    assert message.link == "https://app.nexovue.test/alerts"


def test_a_digest_puts_what_is_still_open_before_what_recovered() -> None:
    held = (
        _notice("resolved", severity="warning", resolved=True),
        _notice("opened", severity="warning"),
    )
    message = compose_batch(held, app_base_url="https://app.nexovue.test")
    assert message.summary == "[warning] 2 alerts"
    assert message.details[0] == "[warning] Checkout API is down"


def test_a_flapping_subject_is_said_once_with_what_happens_next() -> None:
    message = compose(_notice("flapping", openings=5), app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] Checkout API is flapping"
    assert message.details[0].startswith("Checkout API has changed state 5 times in the last hour.")


def test_a_repeat_says_how_long_it_has_been_open() -> None:
    later = dataclasses.replace(_notice("repeated"), created_at=OPENED_AT + timedelta(minutes=95))
    message = compose(later, app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] Checkout API is still down"
    assert message.details[0] == "Checkout API is still down, open for 1 h 35 min."


def _service_notice(kind: str, condition_kind: str, **detail: object) -> Notice:
    return dataclasses.replace(
        _notice(kind),
        condition_kind=condition_kind,
        subject_key="service:checkout",
        subject_label="checkout",
        detail={"service": "checkout", **detail},
    )


def test_a_threshold_says_the_reading_the_threshold_and_the_traffic_behind_it() -> None:
    notice = _service_notice(
        "opened",
        "service_error_rate",
        reading="error rate",
        value=12.345,
        threshold=5,
        unit="%",
        requests=1234,
        window_minutes=5,
    )
    message = compose(notice, app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] checkout error rate is 12.3%, at or over 5%"
    assert message.details[1] == "Over 5 minutes, ending two minutes ago, across 1,234 requests."
    assert message.link == "https://app.nexovue.test/services/checkout"


def test_a_threshold_recovery_says_it_is_back_under() -> None:
    notice = _service_notice(
        "resolved",
        "service_latency_p95",
        reading="p95 latency",
        value=720.0,
        threshold=500,
        unit="ms",
    )
    message = compose(
        dataclasses.replace(notice, resolved_at=OPENED_AT + timedelta(minutes=9)),
        app_base_url="https://app.nexovue.test",
    )
    assert message.summary == "[resolved] checkout p95 latency is back under 500 ms"


def test_a_silent_service_says_since_when() -> None:
    notice = _service_notice("opened", "service_silent", last_seen=OPENED_AT.isoformat())
    message = compose(notice, app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] checkout went silent"
    assert "Nothing received since 2026-09-19 14:03 UTC." in message.details


def test_a_new_error_names_the_exception_and_links_to_the_errors_screen() -> None:
    notice = dataclasses.replace(
        _notice("opened", severity="warning"),
        condition_kind="new_error_group",
        subject_label="KeyError in cart",
        detail={"service": "cart", "services": ["cart", "checkout"], "group_id": "abc"},
    )
    message = compose(notice, app_base_url="https://app.nexovue.test")
    assert message.summary == "[warning] New error: KeyError in cart"
    assert "In: cart, checkout." in message.details
    assert message.link == "https://app.nexovue.test/errors?service_name=cart"


def test_services_going_silent_together_are_not_called_checks() -> None:
    silent = dataclasses.replace(
        _notice("opened"),
        condition_kind="service_silent",
        subject_key="service:checkout",
        subject_label="checkout",
        detail={"service": "checkout", "reading": "silence", "silent_minutes": 10},
    )
    other = dataclasses.replace(silent, subject_key="service:cart", subject_label="cart")
    message = compose_batch((silent, other), app_base_url="https://app.nexovue.test")
    assert "checks" not in message.summary
    assert message.summary == "[critical] 2 alerts"


def test_an_incident_closed_unrecovered_does_not_say_it_recovered() -> None:
    message = compose(
        dataclasses.replace(_notice("closed", resolved=True), rule_id=None),
        app_base_url="https://app.nexovue.test",
    )
    assert message.summary == "[closed] Checkout API is no longer watched"
    assert "Open for: 2 h 5 min." in message.details
    assert not any(line.startswith("Lasted") for line in message.details)
    assert message.details[0] == (
        "Checkout API had not recovered, but nothing watches it any more: its rule or its "
        "check was removed, or the rule stopped covering it."
    )
    assert message.manage_link is None
