"""What the probe task does with a run: confirm, publish, record — or none (AC19, AC20, AC24)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

# The Celery app reads its settings at import, and the task module imports the
# app. Development values, so a private target is reachable (see recording_site).
for _name, _value in {
    "ENV": "development",
    "POSTGRES_DSN": "postgresql://u:p@localhost:5432/d",
    "REDIS_URL": "redis://localhost:6379/1",
    "KAFKA_BROKERS": "localhost:9092",
    "ALLOW_PRIVATE_PROBE_TARGETS": "true",
}.items():
    os.environ.setdefault(_name, _value)

from nexovue_platform.checks.task import RECORD_OUTCOME_TASK, RUN_CHECK_TASK  # noqa: E402
from probe_fixtures import definition, status_below_400  # noqa: E402
from recording_site import RecordingSite, Reply  # noqa: E402

from nexovue_scheduler.celery_app import app  # noqa: E402
from nexovue_scheduler.probes import tasks  # noqa: E402
from nexovue_scheduler.settings import get_settings  # noqa: E402


class _Sent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, name: str, **options: Any) -> None:
        self.calls.append({"name": name, **options})


class _Publisher:
    def __init__(self) -> None:
        self.envelopes: list[tuple[bytes, str | None]] = []

    def publish_many(self, messages: list[tuple[str, str, bytes]]) -> None:
        self.envelopes.extend((envelope, topic) for topic, _, envelope in messages)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, int]] = {}

    def set(self, key: str, value: str, ex: int) -> None:
        self.values[key] = (value, ex)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_Sent, _Publisher, _Redis]]:
    sent, publisher, redis = _Sent(), _Publisher(), _Redis()
    monkeypatch.setattr(app, "send_task", sent)
    monkeypatch.setattr(tasks, "_publisher_for_process", lambda: publisher)
    monkeypatch.setattr(tasks, "_redis_for_process", lambda: redis)
    monkeypatch.setenv("ALLOW_PRIVATE_PROBE_TARGETS", "true")
    monkeypatch.setenv("ENV", "development")
    get_settings.cache_clear()
    yield sent, publisher, redis
    get_settings.cache_clear()


def _run(check: Any, **arguments: Any) -> None:
    tasks.run_check.run(definition=check.model_dump(mode="json"), **arguments)


def test_a_failure_is_confirmed_from_the_next_location_before_state_is_touched(
    harness: tuple[_Sent, _Publisher, _Redis], recording_site: RecordingSite
) -> None:
    sent, publisher, _ = harness
    recording_site.replies["/"] = Reply(status=503)
    check = definition(
        {
            "type": "api",
            "request": {"url": recording_site.url("/"), "assertions": [status_below_400()]},
        },
        locations=["mumbai", "hyderabad"],
        retries=1,
    )

    _run(check, location="mumbai", attempt=1)

    assert [call["name"] for call in sent.calls] == [RUN_CHECK_TASK]
    retry = sent.calls[0]
    assert (retry["queue"], retry["kwargs"]["attempt"], retry["kwargs"]["location"]) == (
        "light.hyderabad",
        2,
        "hyderabad",
    )
    assert len(publisher.envelopes) == 2, "the unconfirmed attempt is still published"


def test_with_one_location_the_confirmation_comes_from_the_same_place(
    harness: tuple[_Sent, _Publisher, _Redis], recording_site: RecordingSite
) -> None:
    """AC20."""
    sent, _, _ = harness
    recording_site.replies["/"] = Reply(status=500)
    check = definition(
        {
            "type": "api",
            "request": {"url": recording_site.url("/"), "assertions": [status_below_400()]},
        },
        locations=["local"],
    )
    _run(check, location="local", attempt=1)
    assert sent.calls[0]["queue"] == "light.local"


def test_the_last_attempt_records_the_outcome_after_publishing(
    harness: tuple[_Sent, _Publisher, _Redis], recording_site: RecordingSite
) -> None:
    sent, publisher, _ = harness
    recording_site.replies["/"] = Reply(status=500)
    check = definition(
        {
            "type": "api",
            "request": {"url": recording_site.url("/"), "assertions": [status_below_400()]},
        },
        retries=1,
    )

    _run(check, location="local", attempt=2)

    assert [call["name"] for call in sent.calls] == [RECORD_OUTCOME_TASK]
    assert sent.calls[0]["kwargs"]["outcome"] == "down"
    assert sent.calls[0]["queue"] == "light"
    assert len(publisher.envelopes) == 2


def test_a_passing_retry_records_up_so_the_state_never_changed(
    harness: tuple[_Sent, _Publisher, _Redis], recording_site: RecordingSite
) -> None:
    """AC19: the failed attempt recorded nothing; only the passing one is recorded."""
    sent, _, _ = harness
    check = definition(
        {
            "type": "api",
            "request": {"url": recording_site.url("/"), "assertions": [status_below_400()]},
        }
    )
    _run(check, location="local", attempt=2)
    assert sent.calls[0]["kwargs"]["outcome"] == "up"


def test_run_now_records_nothing_and_leaves_its_result_for_the_api(
    harness: tuple[_Sent, _Publisher, _Redis], recording_site: RecordingSite
) -> None:
    """AC24."""
    sent, publisher, redis = harness
    recording_site.replies["/"] = Reply(status=500)
    check = definition(
        {
            "type": "api",
            "request": {"url": recording_site.url("/"), "assertions": [status_below_400()]},
        }
    )

    _run(check, location="local", attempt=1, run_now_request_id="req-1")

    assert sent.calls == []
    assert publisher.envelopes == []
    key = f"nexovue:check-run:{check.org_id}:req-1"
    stored, ttl = redis.values[key]
    assert '"outcome":"down"' in stored and ttl > 0


def test_a_run_inside_a_maintenance_window_is_marked_and_changes_no_state(
    harness: tuple[_Sent, _Publisher, _Redis], recording_site: RecordingSite
) -> None:
    """AC23: the run is published, marked, and no state is recorded — even a failure."""
    from datetime import UTC, datetime, timedelta

    from nexovue_platform.checks.task import MaintenanceSchedule

    sent, publisher, _ = harness
    recording_site.replies["/"] = Reply(status=500)
    now = datetime.now(UTC)
    check = definition(
        {
            "type": "api",
            "request": {"url": recording_site.url("/"), "assertions": [status_below_400()]},
        },
    ).model_copy(
        update={
            "maintenance": [
                MaintenanceSchedule(
                    starts_at=(now - timedelta(minutes=5)).isoformat(),
                    ends_at=(now + timedelta(minutes=5)).isoformat(),
                )
            ]
        }
    )

    _run(check, location="local", attempt=2)

    assert sent.calls == []
    assert len(publisher.envelopes) == 2
    evidence = publisher.envelopes[1][0]
    assert b'"maintenance":true' in evidence
