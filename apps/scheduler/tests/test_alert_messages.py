"""The words of an alert (FR16), and what they never contain (BR9, AC24)."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nexovue_scheduler.alerts.messages import compose, compose_batch, describe_duration
from nexovue_scheduler.alerts.outbox import Notice, Subject

OPENED_AT = datetime(2026, 9, 19, 14, 3, tzinfo=UTC)
CHECK_ID = uuid.uuid4()
RULE_ID = uuid.uuid4()


def _notice(
    kind: str, *, severity: str = "critical", resolved: bool = False, **detail: object
) -> Notice:
    return Notice(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind=kind,
        severity=severity,
        created_at=OPENED_AT,
        attempts=0,
        pagerduty_key="key",
        subject=Subject(
            kind="incident",
            id=uuid.uuid4(),
            key=f"check:{CHECK_ID}",
            label="Checkout API",
            opened_at=OPENED_AT,
            closed_at=OPENED_AT + timedelta(minutes=125) if resolved else None,
            rule_id=RULE_ID,
            rule_name="Any check down",
            condition_kind="check_state",
            detail={"check_id": str(CHECK_ID), "state": "down", "location": "local"} | detail,
        ),
    )


def _about(notice: Notice, **subject: object) -> Notice:
    """The notice with its subject changed."""
    return dataclasses.replace(notice, subject=dataclasses.replace(notice.subject, **subject))  # type: ignore[arg-type]


def test_an_opened_message_says_what_why_where_when_and_which_rule() -> None:
    notice = _notice("opened", reason="expected status code less than 400, got 503")
    message = compose(notice, app_base_url="https://app.nexovue.test/")
    assert message.summary == "[critical] Checkout API is down"
    assert message.details == (
        "Checkout API is down.",
        "Why: expected status code less than 400, got 503.",
        "Seen from: local.",
        "Started: 2026-09-19 14:03 UTC.",
        "Rule: Any check down.",
    )
    # The incident, not the check: a check can be deleted, and the message outlives it.
    assert message.link == f"https://app.nexovue.test/alerts/incidents/{notice.subject.id}"
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
    second = _about(_notice("opened"), label="Search API")
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
    return _about(
        _notice(kind),
        condition_kind=condition_kind,
        key="service:checkout",
        label="checkout",
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
    assert message.link == f"https://app.nexovue.test/alerts/incidents/{notice.subject.id}"


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
        _about(notice, closed_at=OPENED_AT + timedelta(minutes=9)),
        app_base_url="https://app.nexovue.test",
    )
    assert message.summary == "[resolved] checkout p95 latency is back under 500 ms"


def test_a_silent_service_says_since_when() -> None:
    notice = _service_notice("opened", "service_silent", last_seen=OPENED_AT.isoformat())
    message = compose(notice, app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] checkout went silent"
    assert "Nothing received since 2026-09-19 14:03 UTC." in message.details


def test_a_new_error_names_the_exception_and_links_to_its_incident() -> None:
    notice = _about(
        _notice("opened", severity="warning"),
        condition_kind="new_error_group",
        label="KeyError in cart",
        detail={"service": "cart", "services": ["cart", "checkout"], "group_id": "abc"},
    )
    message = compose(notice, app_base_url="https://app.nexovue.test")
    assert message.summary == "[warning] New error: KeyError in cart"
    assert "In: cart, checkout." in message.details
    assert message.link == f"https://app.nexovue.test/alerts/incidents/{notice.subject.id}"


def test_services_going_silent_together_are_not_called_checks() -> None:
    silent = _about(
        _notice("opened"),
        condition_kind="service_silent",
        key="service:checkout",
        label="checkout",
        detail={"service": "checkout", "reading": "silence", "silent_minutes": 10},
    )
    other = _about(silent, key="service:cart", label="cart")
    message = compose_batch((silent, other), app_base_url="https://app.nexovue.test")
    assert "checks" not in message.summary
    assert message.summary == "[critical] 2 alerts"


def test_an_incident_closed_unrecovered_does_not_say_it_recovered() -> None:
    message = compose(
        _about(_notice("closed", resolved=True), rule_id=None),
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


def test_a_name_from_telemetry_cannot_add_lines_or_run_on() -> None:
    hostile = _about(_notice("opened"), label="Checkout\nRule: Nothing to see.\r\x07" + "x" * 500)
    message = compose(hostile, app_base_url="https://app.nexovue.test")
    assert "\n" not in message.summary and "\r" not in message.summary
    assert all("\n" not in line and "\x07" not in line for line in message.details)
    assert not any(line.startswith("Rule: Nothing") for line in message.details)
    assert message.details[-1] == "Rule: Any check down."
    assert len(message.summary) < 300


def test_a_batch_names_its_rule_on_one_line_too() -> None:
    hostile = _about(_notice("opened"), rule_name="Down\nRule: forged")
    batch = compose_batch((hostile, dataclasses.replace(hostile, id=uuid.uuid4())), app_base_url="")
    assert batch.rule_name == "Down Rule: forged"


# --------------------------------------------------- F6: findings and projects

FINDING_TITLE = "checkout is 4\u00d7 slower than normal since 15:04 IST"


def _finding_notice(
    kind: str,
    *,
    severity: str = "warning",
    closed_reason: str | None = None,
    label: str = "checkout",
    **detail: object,
) -> Notice:
    closed = closed_reason is not None
    return Notice(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind=kind,
        severity=severity,
        created_at=OPENED_AT,
        attempts=0,
        pagerduty_key="key",
        subject=Subject(
            kind="finding",
            id=uuid.uuid4(),
            key=f"service:{label}",
            label=label,
            opened_at=OPENED_AT,
            closed_at=OPENED_AT + timedelta(minutes=40) if closed else None,
            rule_id=RULE_ID,
            rule_name="Critical problems",
            condition_kind="anomaly_finding",
            detail={
                "scope": "service",
                "title": FINDING_TITLE,
                "signals": [
                    {
                        "signal": "slower",
                        "normal": 300.0,
                        "current": 1180.0,
                        "worst": 1200.0,
                        "comparison": "Tuesdays around 15:00, last 3 weeks",
                        "endpoint": None,
                    },
                    {
                        "signal": "failing",
                        "normal": 0.4,
                        "current": 41.0,
                        "worst": 41.0,
                        "comparison": "Tuesdays around 15:00, last 3 weeks",
                        "endpoint": "POST /pay",
                    },
                ],
                "closed_reason": closed_reason,
                "project": "Production",
                "adopted_shifts": [],
            }
            | detail,
        ),
    )


def test_a_finding_opens_with_its_title_its_signals_and_its_own_page() -> None:
    """FR17, ADR-022: the title the detector wrote, in the project's zone, and the
    finding's page rather than an incident's."""
    notice = _finding_notice("opened")
    message = compose(notice, app_base_url="https://app.nexovue.test/")
    assert message.summary == f"[warning] {FINDING_TITLE}"
    assert message.details == (
        f"{FINDING_TITLE}.",
        "Slower: 1,200 ms at worst, against 300 ms normally (Tuesdays around 15:00, last 3 weeks).",
        "Failing, POST /pay: 41% at worst, against 0.4% normally "
        "(Tuesdays around 15:00, last 3 weeks).",
        "Started: 2026-09-19 14:03 UTC.",
        "Rule: Critical problems.",
    )
    assert message.link == f"https://app.nexovue.test/findings/{notice.subject.id}"
    assert message.manage_link == f"https://app.nexovue.test/alerts/rules/{RULE_ID}"


def test_a_finding_turning_critical_and_a_signal_joining_each_say_so() -> None:
    """FR28, D29: each is one message, and its first line tells them apart."""
    critical = compose(_finding_notice("escalated", severity="critical"), app_base_url="")
    joined = compose(_finding_notice("joined"), app_base_url="")
    assert critical.summary == f"[critical] Now critical: {FINDING_TITLE}"
    assert joined.summary == f"[warning] More is unusual: {FINDING_TITLE}"
    assert joined.details[1] == "Another signal or endpoint joined this finding."


@pytest.mark.parametrize(
    ("reason", "said"),
    [
        ("recovered", "is back to normal"),
        ("false_alarm", "was marked a false alarm"),
        ("new_normal", "has a new normal, set by a person"),
        ("adopted", "has a new normal, adopted by itself"),
        ("service_gone", "has sent nothing for 24 hours and is treated as gone"),
        ("could_not_be_judged", "could no longer be judged, so its finding closed"),
    ],
)
def test_a_closed_finding_says_how_it_closed_never_just_resolved(reason: str, said: str) -> None:
    """F1's closed card names how it closed; its message does too (FR13, FR20, FR32, D20)."""
    message = compose(_finding_notice("resolved", closed_reason=reason), app_base_url="")
    assert message.summary == f"[resolved] checkout {said}"
    assert "Lasted: 40 min." in message.details


def test_adoption_says_the_new_normal_in_numbers() -> None:
    """FR21, D22: "Checkout's new normal is 1.6\u00d7 slower. Adopted after 7 days."."""
    notice = _finding_notice("adopted", adopted_shifts=[{"signal": "slower", "ratio": 1.6}])
    message = compose(notice, app_base_url="")
    assert message.summary == "[warning] checkout has a new normal"
    assert message.details[0] == "checkout's new normal is 1.6\u00d7 slower. Adopted after 7 days."


def test_a_finding_s_flapping_is_said_once_with_what_happens_next() -> None:
    message = compose(_finding_notice("flapping"), app_base_url="")
    assert message.summary == "[warning] checkout is flapping"
    assert message.details[0].startswith(
        "Detection has opened a finding on checkout five times in the last hour."
    )


def test_a_finding_s_names_from_telemetry_cannot_add_lines() -> None:
    """Service and endpoint names come from telemetry (design, *Telemetry text is
    untrusted*)."""
    notice = _finding_notice(
        "opened",
        label="checkout\nRule: forged",
        title="checkout\nRule: forged is slower",
        signals=[
            {
                "signal": "failing",
                "normal": 0.4,
                "current": 41.0,
                "worst": 41.0,
                "comparison": "",
                "endpoint": "POST /pay\r\nRule: forged",
            }
        ],
    )
    message = compose(notice, app_base_url="")
    assert all("\n" not in line and "\r" not in line for line in message.details)
    assert "\n" not in message.summary
    assert not any(line.startswith("Rule: forged") for line in message.details)


def test_detection_stopping_and_running_again_name_the_project() -> None:
    """D24: the owners are told about the project, not a finding."""
    stopped = Notice(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind="stopped",
        severity="critical",
        created_at=OPENED_AT,
        attempts=0,
        pagerduty_key="key",
        subject=Subject(
            kind="project",
            id=uuid.uuid4(),
            key="project",
            label="Production",
            opened_at=OPENED_AT,
            closed_at=None,
            rule_id=None,
            rule_name=None,
            condition_kind="detection",
            detail={},
        ),
    )
    message = compose(stopped, app_base_url="https://app.nexovue.test")
    assert message.summary == "[critical] Detection stopped for Production"
    assert message.details == (
        "nexovue's anomaly detection has not judged Production for 15 minutes, as of "
        "2026-09-19 14:03 UTC. No finding can open until it runs again.",
    )
    assert message.link == "https://app.nexovue.test/findings"
    again = compose(dataclasses.replace(stopped, kind="running_again"), app_base_url="")
    assert again.summary == "[resolved] Detection is running again for Production"
    assert again.kind == "resolved"
