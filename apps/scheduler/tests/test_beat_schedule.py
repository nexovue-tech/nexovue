"""An edited check reaches the worker (P1-33).

The whole reason the schedule lives in a table is that a customer can change a
check and see it take effect without anyone deploying. The first version
compared only the interval when deciding whether to keep a running entry — so a
changed target or config was carried in the old entry's kwargs and never reached
the worker at all, while the interval alone appeared to work.

Observed in the dev stack before the fix: a browser check's step list was
corrected in Postgres, and the flow kept running the previous one.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from celery.schedules import schedule as run_every

from nexovue_scheduler.beat import PostgresScheduler, _unchanged
from nexovue_scheduler.checks import CheckDefinition

ORG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
CHECK_ID = uuid.uuid4()


def _check(
    *, target: str, interval: int = 60, config: dict[str, object] | None = None
) -> CheckDefinition:
    return CheckDefinition(
        id=CHECK_ID,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        type="http",
        target=target,
        interval_seconds=interval,
        config=config or {},
    )


def _scheduler() -> PostgresScheduler:
    """Only the state `_entries_for` touches; the constructor wants Redis."""
    scheduler = PostgresScheduler.__new__(PostgresScheduler)
    scheduler._entries = {}
    scheduler.app = None
    return scheduler


def test_an_edited_target_reaches_the_worker() -> None:
    scheduler = _scheduler()
    scheduler._entries = scheduler._entries_for([_check(target="https://before.example")])

    rebuilt = scheduler._entries_for([_check(target="https://after.example")])

    name = f"check:{CHECK_ID}"
    assert rebuilt[name].kwargs["target"] == "https://after.example"


def test_an_edited_config_reaches_the_worker() -> None:
    """The case seen live: a browser check's steps changed and never took."""
    scheduler = _scheduler()
    scheduler._entries = scheduler._entries_for(
        [_check(target="https://a.example", config={"steps": ["old"]})]
    )

    rebuilt = scheduler._entries_for(
        [_check(target="https://a.example", config={"steps": ["new"]})]
    )

    assert rebuilt[f"check:{CHECK_ID}"].kwargs["config"] == {"steps": ["new"]}


def test_an_unchanged_check_keeps_its_running_entry() -> None:
    """The reason the comparison exists at all.

    Rebuilding every refresh would reset each entry's clock, and a 30s refresh
    would stop a 60s check from ever coming due.
    """
    scheduler = _scheduler()
    first = scheduler._entries_for([_check(target="https://a.example")])
    scheduler._entries = first

    second = scheduler._entries_for([_check(target="https://a.example")])

    name = f"check:{CHECK_ID}"
    assert second[name] is first[name]


def test_a_browser_check_is_scheduled_as_its_own_task() -> None:
    """The task name is what puts it on the browser queue.

    Both check types once shared one task name, so browser flows were routed to
    the light worker — where Playwright is deliberately not installed. Every
    flow failed on an import, and the queue separation that exists to keep a
    slow browser away from sub-second checks did nothing at all.
    """
    scheduler = _scheduler()
    http_check = _check(target="https://a.example")
    flow = CheckDefinition(
        id=uuid.uuid4(),
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        type="browser",
        target="https://a.example",
        interval_seconds=60,
        config={},
    )

    entries = scheduler._entries_for([http_check, flow])

    assert entries[f"check:{http_check.id}"].task == "nexovue_scheduler.probes.run_check"
    assert entries[f"check:{flow.id}"].task == "nexovue_scheduler.probes.browser.run_flow"


def test_the_comparison_looks_at_arguments_as_well_as_timing() -> None:
    """Directly, because this is the predicate the bug lived in."""
    entries = _scheduler()._entries_for([_check(target="https://a.example")])
    entry = entries[f"check:{CHECK_ID}"]
    interval = run_every(run_every=timedelta(seconds=60))

    assert _unchanged(entry, interval, entry.kwargs)
    assert not _unchanged(entry, interval, entry.kwargs | {"target": "https://b.example"})
    assert not _unchanged(entry, run_every(run_every=timedelta(seconds=120)), entry.kwargs)
