"""Run now and Send test through the job queue, from the API's side (ADR-011, ADR-020).

The worker's side is `apps/scheduler/tests/test_jobs_live.py`. This holds what
the API owns: the job it adds — on the right queue, for the right organization,
expiring if nobody takes it, and waking a worker because a person is waiting —
and that a result is read only by the organization that asked.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import asyncpg
import pytest
from live import require_stack
from nexovue_platform.alerts.tasks import DELIVERIES_QUEUE, SEND_TEST_TASK
from nexovue_platform.checks.task import RUN_CHECK_TASK, CheckDefinition
from nexovue_platform.jobs import NOTIFY_CHANNEL
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexovue_api.channels.send_test import JobsChannelTester
from nexovue_api.checks.run_now import JobsProbeBroker
from nexovue_api.data.postgres.models import Job, JobResult, Organization
from nexovue_api.signup import service as signup

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
# A location no worker serves, so the dev stack never takes these jobs.
LOCATION: Final = f"test-{uuid.uuid4().hex[:8]}"


def _reachable() -> bool:
    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_job_dispatch_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def organizations(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[list[str]]:
    """Two committed tenants, removed afterwards with everything queued for them."""
    made: list[uuid.UUID] = []
    async with factory.begin() as db:
        for _ in range(2):
            suffix = uuid.uuid4().hex[:10]
            tenant = await signup.provision_tenant(
                db,
                organization_name=f"Dispatch {suffix}",
                email=f"dispatch-{suffix}@nexovue.test",
                password="correct-horse-battery",
                name=None,
            )
            made.append(tenant.organization.id)
    yield [str(org_id) for org_id in made]
    async with factory.begin() as db:
        await db.execute(delete(Organization).where(Organization.id.in_(made)))


def _definition(org_id: str) -> CheckDefinition:
    return CheckDefinition(
        check_id=None,
        org_id=org_id,
        project_id=str(uuid.uuid4()),
        name="run now",
        type="tcp",
        config={"type": "tcp", "host": "example.com", "port": 443},
        interval_seconds=60,
        locations=[LOCATION],
    )


async def _queued(factory: async_sessionmaker[AsyncSession], org_id: str) -> list[Job]:
    async with factory() as db:
        return list(await db.scalars(select(Job).where(Job.org_id == uuid.UUID(org_id))))


async def test_run_now_adds_a_job_on_its_locations_queue_that_expires_unclaimed(
    factory: async_sessionmaker[AsyncSession], organizations: list[str]
) -> None:
    org_id = organizations[0]

    await JobsProbeBroker(factory).dispatch(_definition(org_id), location=LOCATION, request_id="r")

    [job] = await _queued(factory, org_id)
    assert (job.task, job.queue) == (RUN_CHECK_TASK, f"light.{LOCATION}")
    assert job.kwargs["run_now_request_id"] == "r"
    assert job.expires_at is not None
    assert job.expires_at - job.created_at <= timedelta(seconds=61)
    assert job.hard_limit_seconds > job.soft_limit_seconds


async def test_a_send_test_adds_a_job_on_the_deliveries_queue(
    factory: async_sessionmaker[AsyncSession], organizations: list[str]
) -> None:
    org_id = organizations[0]

    await JobsChannelTester(factory).dispatch(
        org_id=org_id, channel_id=str(uuid.uuid4()), request_id=str(uuid.uuid4())
    )

    [job] = await _queued(factory, org_id)
    assert (job.task, job.queue) == (SEND_TEST_TASK, DELIVERIES_QUEUE)


async def test_a_job_somebody_waits_on_wakes_the_workers(
    factory: async_sessionmaker[AsyncSession], organizations: list[str]
) -> None:
    heard: asyncio.Queue[str] = asyncio.Queue()
    listener = await asyncpg.connect(POSTGRES_DSN)
    try:
        await listener.add_listener(NOTIFY_CHANNEL, lambda *args: heard.put_nowait(args[3]))
        await JobsProbeBroker(factory).dispatch(
            _definition(organizations[0]), location=LOCATION, request_id="r"
        )
        assert await asyncio.wait_for(heard.get(), timeout=5) == f"light.{LOCATION}"
    finally:
        await listener.close()


async def test_a_result_is_read_only_by_the_organization_that_asked(
    factory: async_sessionmaker[AsyncSession], organizations: list[str]
) -> None:
    """A request id is a UUID a client holds; knowing another tenant's buys nothing."""
    mine, theirs = organizations
    request_id = uuid.uuid4()
    async with factory.begin() as db:
        db.add(
            JobResult(
                request_id=request_id,
                org_id=uuid.UUID(mine),
                kind="channel_test",
                payload=json.loads('{"delivered": true, "http_status": 200, "reason": null}'),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
    tester = JobsChannelTester(factory)

    found = await tester.collect(org_id=mine, request_id=str(request_id))
    assert found is not None and found.delivered and found.http_status == 200
    assert await tester.collect(org_id=theirs, request_id=str(request_id)) is None
    # A different kind under the same id is not this result either.
    assert await JobsProbeBroker(factory).collect(org_id=mine, request_id=str(request_id)) is None
    assert await tester.collect(org_id=mine, request_id="not-a-uuid") is None
