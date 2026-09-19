"""Beat schedules each check from each location, and an edit reaches the worker.

The whole reason the schedule lives in a table is that a customer can change a
check and see it take effect without anyone deploying. The first version
compared only the interval when deciding whether to keep a running entry — so a
changed target or config was carried in the old entry's kwargs and never reached
the worker at all, while the interval alone appeared to work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from celery.schedules import schedule as run_every
from nexovue_platform.checks.task import CheckDefinition

from nexovue_scheduler.beat import (
    ATTEMPT_PRUNE_ENTRY,
    DELIVERY_SWEEP_ENTRY,
    FILE_SWEEP_ENTRY,
    HEARTBEAT_SWEEP_ENTRY,
    PostgresScheduler,
    _start_offset,
    _unchanged,
)
from nexovue_scheduler.checks import ScheduledCheck

ORG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
CHECK_ID = uuid.uuid4()


def _config(url: str) -> dict[str, Any]:
    return {
        "type": "api",
        "request": {
            "url": url,
            "timeout_seconds": 7,
            "assertions": [{"source": "status_code", "comparison": "less_than", "target": "400"}],
        },
    }


def _check(
    *,
    url: str = "https://a.example",
    interval: int = 60,
    locations: list[str] | None = None,
    check_type: str = "api",
    check_id: uuid.UUID = CHECK_ID,
) -> ScheduledCheck:
    config = {"type": "heartbeat"} if check_type == "heartbeat" else _config(url)
    return ScheduledCheck(
        id=check_id,
        type=check_type,
        interval_seconds=interval,
        definition=CheckDefinition(
            check_id=str(check_id),
            org_id=str(ORG_ID),
            project_id=str(PROJECT_ID),
            name="a",
            type=check_type,
            config=config,
            interval_seconds=interval,
            locations=locations or ["mumbai"],
        ),
        created_at=datetime.now(UTC),
        last_ping_at=None,
    )


def _scheduler() -> PostgresScheduler:
    """Only the state `_entries_for` touches; the constructor wants Redis."""
    scheduler = PostgresScheduler.__new__(PostgresScheduler)
    scheduler._entries = {}
    scheduler.app = None
    scheduler._settings = SimpleNamespace(  # type: ignore[assignment]  # only this field is read
        probe_locations=["mumbai", "hyderabad", "elsewhere"]
    )
    return scheduler


def test_a_check_is_scheduled_once_per_location_on_that_locations_queue() -> None:
    entries = _scheduler()._entries_for([_check(locations=["mumbai", "hyderabad"])])

    mumbai = entries[f"check:{CHECK_ID}:mumbai"]
    hyderabad = entries[f"check:{CHECK_ID}:hyderabad"]
    assert mumbai.options["queue"] == "light.mumbai"
    assert hyderabad.options["queue"] == "light.hyderabad"
    assert mumbai.kwargs["location"] == "mumbai"


def test_a_run_expires_with_its_interval_and_is_limited_by_its_own_timeouts() -> None:
    """FR26: a run not started before the next is due is dropped, not queued."""
    entry = _scheduler()._entries_for([_check(interval=300)])[f"check:{CHECK_ID}:mumbai"]
    assert entry.options["expires"] == 300
    assert entry.options["soft_time_limit"] > 7
    assert entry.options["time_limit"] > entry.options["soft_time_limit"]


def test_an_edited_url_reaches_the_worker() -> None:
    scheduler = _scheduler()
    scheduler._entries = scheduler._entries_for([_check(url="https://before.example")])

    rebuilt = scheduler._entries_for([_check(url="https://after.example")])

    config = rebuilt[f"check:{CHECK_ID}:mumbai"].kwargs["definition"]["config"]
    assert config["request"]["url"] == "https://after.example"


def test_an_unchanged_check_keeps_its_running_entry() -> None:
    """Rebuilding every refresh would reset each entry's clock, and a 30s refresh
    would stop a 60s check from ever coming due."""
    scheduler = _scheduler()
    first = scheduler._entries_for([_check()])
    scheduler._entries = first

    second = scheduler._entries_for([_check()])

    name = f"check:{CHECK_ID}:mumbai"
    assert second[name] is first[name]


def test_a_heartbeat_has_no_probe_entry_and_the_sweeps_are_always_scheduled() -> None:
    scheduler = _scheduler()
    entries = scheduler._entries_for([_check(check_type="heartbeat")]) | scheduler._sweep_entries()

    assert not any(name.startswith("check:") for name in entries)
    assert entries[HEARTBEAT_SWEEP_ENTRY].options["queue"] == "light"
    assert FILE_SWEEP_ENTRY in entries


def test_the_alert_sweeps_are_scheduled_on_their_own_queues() -> None:
    """F21. The delivery sweep is the outbox's safety net (ADR-013), and runs on the
    deliveries queue so its sends never take a probe's slot."""
    entries = _scheduler()._sweep_entries()

    delivery = entries[DELIVERY_SWEEP_ENTRY]
    assert delivery.options["queue"] == "deliveries"
    assert delivery.schedule.run_every.total_seconds() == 15
    assert entries[ATTEMPT_PRUNE_ENTRY].options["queue"] == "light"


def test_a_location_this_deployment_does_not_have_is_never_dispatched_to() -> None:
    """A queue nothing consumes grows for ever — the browser queue's defect (spec)."""
    entries = _scheduler()._entries_for([_check(locations=["mumbai", "mars"])])
    assert set(entries) == {f"check:{CHECK_ID}:mumbai"}


def test_checks_do_not_all_start_on_the_same_second() -> None:
    offsets = {_start_offset(_check(check_id=uuid.uuid4())) for _ in range(20)}
    assert len(offsets) > 1
    assert all(timedelta(0) <= offset < timedelta(seconds=60) for offset in offsets)


def test_the_comparison_looks_at_arguments_as_well_as_timing() -> None:
    entries = _scheduler()._entries_for([_check()])
    entry = entries[f"check:{CHECK_ID}:mumbai"]
    interval = run_every(run_every=timedelta(seconds=60))

    assert _unchanged(entry, interval, entry.kwargs)
    assert not _unchanged(entry, interval, entry.kwargs | {"location": "elsewhere"})
    assert not _unchanged(entry, run_every(run_every=timedelta(seconds=120)), entry.kwargs)
