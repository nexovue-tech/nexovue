"""The bed's live readings, on real Postgres and ClickHouse (F3 plan T2.16; AC16(b), AC97).

- A sampled healthy window is read through the evidence job's own facts read, and a signed deploy
  just before it is found, so a clean healthy result means the read worked and nothing was there
  (AC16(b): "seeded data alone cannot prove this");
- the forged run's observer finds a finding the service really has, and says so when none opened;
- **100 findings opened at once** run through the real worker and the real `gather` task on a
  lane shaped like `scheduler-light` (evidence capped, a floor kept), reporting how long the queue
  took to clear and how long a due probe waited (AC97).

Every test owns its tenant and its lanes, and removes them. The lanes carry this run's name, so the
stack's own workers never claim these jobs and this test's workers never claim theirs.
This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import job_fixtures
import pytest
from alert_fixtures import (
    ASYNC_DSN,
    POSTGRES_DSN,
    AlertTenant,
    add_finding,
    drop_tenant,
    make_tenant,
    postgres_reachable,
)
from at_once import render, summarize
from child_env import children_read_this_tests_environment  # noqa: F401  fixture
from clickhouse_cleanup import delete_after_run
from detection_history import READER_DSN
from forged import Observed, Spec
from healthy import SampledWindow, run_healthy
from live import require_stack
from live_reader import finding_observer, live_reader
from nexovue_platform.evidence import (
    EVIDENCE_CONCURRENCY,
    OPENED,
    RULE_SIGNED_DEPLOY,
    RULE_SIGNED_DEPLOY_WEAK,
    gather_evidence_request,
)
from nexovue_platform.jobs import JobRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_scheduler.evidence.store import EvidenceStore, connect_store
from nexovue_scheduler.jobs.queue import enqueue
from nexovue_scheduler.jobs.worker import Worker

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_bed_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

SERVICE: Final = "checkout"
MACHINE: Final = "a shared build machine, own Postgres and ClickHouse containers"
FINDINGS_AT_ONCE: Final = 100
# `scheduler-light`'s shape: eight slots, four kept for probes and alert sweeps (worker.py).
SLOTS: Final = 8
FLOOR: Final = 4
PATIENCE_SECONDS: Final = 120.0
# The poll is a second and a child starts in well under another: the floor keeps slots free.
PROBE_MUST_START_WITHIN: Final = timedelta(seconds=5)
MODULES: Final = ("nexovue_scheduler.evidence.gather", "job_fixtures")


@pytest.fixture
def now() -> datetime:
    """Read per test, never at import: a stack sweep reads the real clock (testing.md)."""
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(ASYNC_DSN, pool_size=6)
    try:
        yield built
    finally:
        await built.dispose()


@pytest.fixture
async def own(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(engine, with_check=False)
    for table in ("service_edges_1m", "spans", "logs"):
        delete_after_run(table, str(made.org_id))
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


@pytest.fixture
def telemetry() -> EvidenceStore:
    return connect_store(READER_DSN)


async def signed_deploy(
    engine: AsyncEngine, tenant: AlertTenant, *, service: str, version: str, at: datetime
) -> None:
    """A signed deploy as F28 stores it: the release it filed under, and the event."""
    async with engine.begin() as connection:
        release_id = await connection.scalar(
            text(
                """
                INSERT INTO releases (org_id, project_id, service_name, version, first_reported_at,
                                      last_reported_at, report_count, last_state, last_activity_at)
                VALUES (:org_id, :project_id, :service, :version, :at, :at, 1, 'finished', now())
                RETURNING id
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "service": service,
                "version": version,
                "at": at,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO deploy_events (org_id, project_id, version, service_name, deployed_at,
                                           source, delivery_id, metadata, state, release_id)
                VALUES (:org_id, :project_id, :version, :service, :at, 'ci', :delivery,
                        '{}', 'finished', :release_id)
                """
            ),
            {
                "org_id": tenant.org_id,
                "project_id": tenant.project_id,
                "version": version,
                "service": service,
                "at": at,
                "delivery": str(uuid.uuid4()),
                "release_id": release_id,
            },
        )


# ---------------------------------------------------------------- the healthy window, really read


async def test_a_sampled_window_reads_the_deploy_that_lines_up_and_nothing_where_none_does(
    engine: AsyncEngine, own: AlertTenant, telemetry: EvidenceStore, now: datetime
) -> None:
    """AC16(b): the sampled window goes through the job's own read, so a window with a deploy four
    minutes before names it and a quiet one names nothing, with no read failed on either."""
    lined_up = now - timedelta(hours=3)
    quiet = now - timedelta(hours=9)
    # Records go back 9 days, on hours that are not these windows', so the base rate is low.
    for days_back in range(1, 10):
        await signed_deploy(
            engine,
            own,
            service=SERVICE,
            version=f"old-{days_back}",
            at=now - timedelta(days=days_back, hours=1),
        )
    await signed_deploy(
        engine, own, service=SERVICE, version="v-lined-up", at=lined_up - timedelta(minutes=4)
    )
    reader = live_reader(
        engine,
        telemetry,
        org_id=own.org_id,
        project_id=own.project_id,
        now=lined_up + timedelta(minutes=5),
    )
    quiet_reader = live_reader(
        engine,
        telemetry,
        org_id=own.org_id,
        project_id=own.project_id,
        now=quiet + timedelta(minutes=5),
    )

    with_deploy = await run_healthy(reader, [SampledWindow(SERVICE, lined_up)])
    without = await run_healthy(quiet_reader, [SampledWindow(SERVICE, quiet)])

    assert with_deploy.windows_with_failed_reads == 0 and without.windows_with_failed_reads == 0
    assert [each.rule for each in with_deploy.false_causes] and with_deploy.false_causes[
        0
    ].rule in {
        RULE_SIGNED_DEPLOY,
        RULE_SIGNED_DEPLOY_WEAK,
    }
    assert without.clean


# ---------------------------------------------------------------- the forged run's observer


async def test_the_observer_finds_the_services_own_finding_and_its_cause_and_says_when_none_opened(
    engine: AsyncEngine, own: AlertTenant, telemetry: EvidenceStore, now: datetime
) -> None:
    started = now - timedelta(minutes=10)
    for days_back in range(1, 10):
        await signed_deploy(
            engine,
            own,
            service=SERVICE,
            version=f"old-{days_back}",
            at=now - timedelta(days=days_back, hours=4),
        )
    await signed_deploy(
        engine, own, service=SERVICE, version="v-before", at=started - timedelta(minutes=4)
    )
    async with engine.begin() as connection:
        await add_finding(connection, own, service=SERVICE, started_at=started)
    reader = live_reader(engine, telemetry, org_id=own.org_id, project_id=own.project_id, now=now)
    observe = finding_observer(
        engine, reader, org_id=own.org_id, project_id=own.project_id, wait=1, poll_seconds=0.2
    )

    found = await observe(Spec("new_version", SERVICE, started, outage=True))
    none = await observe(Spec("new_version", "payment", started, outage=False))

    assert isinstance(found, Observed) and found.finding_opened and found.cause is not None
    assert none == Observed(False, None)


# ---------------------------------------------------------------- 100 findings at once


async def _explained_at(engine: AsyncEngine, tenant: AlertTenant) -> dict[uuid.UUID, datetime]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT finding_id, read_at FROM finding_explanations "
                "WHERE org_id = :org_id AND version = 1"
            ),
            {"org_id": tenant.org_id},
        )
        return {row.finding_id: row.read_at for row in rows}


@pytest.mark.usefixtures("children_read_this_tests_environment")
async def test_one_hundred_findings_at_once_clear_and_a_due_probe_does_not_wait_behind_them(
    engine: AsyncEngine,
    own: AlertTenant,
    telemetry: EvidenceStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC97, NFR2: 100 findings open in one instant, their gathers run by a real worker on a lane
    shaped like `scheduler-light`, and a probe falls due while they run. Every finding is explained,
    and the probe runs during the burst without waiting behind the evidence queue."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("POSTGRES_DSN", ASYNC_DSN)
    monkeypatch.setenv("CLICKHOUSE_DSN", READER_DSN)
    # The role publishes to Kafka, so its settings need an address; nothing here sends to it.
    monkeypatch.setenv("KAFKA_BROKERS", "localhost:29092")
    suffix = uuid.uuid4().hex
    evidence_lane, light_lane = f"bed-live.evidence.{suffix}", f"bed-live.light.{suffix}"
    opened = datetime.now(UTC)
    finding_ids: list[uuid.UUID] = []
    async with engine.begin() as connection:
        # One open finding per service is the table's rule, so each burst finding has its own.
        for number in range(FINDINGS_AT_ONCE):
            service = f"burst-{number:03d}"
            finding_ids.append(
                await add_finding(connection, own, service=service, started_at=opened)
            )
    notes = tmp_path / "probe"
    stop = asyncio.Event()
    worker = Worker(
        engine,
        listen_dsn=POSTGRES_DSN,
        queues=[evidence_lane, light_lane],
        concurrency=SLOTS,
        task_modules=MODULES,
        caps={evidence_lane: EVIDENCE_CONCURRENCY},
        uncapped_floor=FLOOR,
    )
    running = asyncio.create_task(worker.run(stop))
    try:
        async with engine.begin() as connection:
            for finding_id in finding_ids:
                request = gather_evidence_request(finding_id, own.org_id, OPENED)
                await enqueue(connection, replace(request, queue=evidence_lane))
        probe_due = datetime.now(UTC)
        async with engine.begin() as connection:
            await enqueue(
                connection,
                _probe_request(light_lane, notes),
            )
        probe_ran: datetime | None = None
        deadline = time.monotonic() + PATIENCE_SECONDS
        explained: dict[uuid.UUID, datetime] = {}
        while time.monotonic() < deadline:
            if probe_ran is None and notes.exists():
                probe_ran = datetime.now(UTC)
            explained = await _explained_at(engine, own)
            if len(explained) == FINDINGS_AT_ONCE and probe_ran is not None:
                break
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.gather(running)
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM jobs WHERE queue = ANY(:lanes)"),
                {"lanes": [evidence_lane, light_lane]},
            )

    result = summarize(
        started_at=[opened] * FINDINGS_AT_ONCE,
        explained_at=[explained.get(each) for each in finding_ids],
        probe_due=probe_due,
        probe_ran=probe_ran,
    )
    sys.stdout.write("\nAC97, one run:\n" + render(result, machine=MACHINE) + "\n")
    output = _output_path()
    if output is not None:
        output.write_text(render(result, machine=MACHINE) + "\n", encoding="utf-8")

    assert result.explained == FINDINGS_AT_ONCE, "the queue never cleared"
    assert result.probe_ran_during_the_burst, "the probe ran after the burst, which proves nothing"
    assert result.probe_waited is not None
    assert result.probe_waited < PROBE_MUST_START_WITHIN
    assert result.cleared_after is not None and result.cleared_after > result.probe_waited


def _probe_request(lane: str, notes: Path) -> JobRequest:
    return JobRequest(
        task=job_fixtures.NOTE,
        queue=lane,
        kwargs={"path": str(notes), "marker": "probe"},
        expires_seconds=60,
    )


def _output_path() -> Path | None:
    value = os.environ.get("AI_EVAL_AT_ONCE_OUT")
    return Path(value) if value else None
