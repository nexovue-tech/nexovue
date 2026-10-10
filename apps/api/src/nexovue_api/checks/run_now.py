"""Run now: hand a definition to a probe worker and collect what it saw (ADR-011).

The API never makes the request itself — outbound calls to addresses a customer
chose stay off the control plane, beside the database credentials. It adds a job
by name, so no scheduler code is imported, wakes the workers because a person is
waiting, and reads the result the worker leaves in `job_results` — found only
under the organization that asked.
"""

from __future__ import annotations

import uuid
from typing import Final, Protocol

from nexovue_platform.checks.evidence import RunEvidence
from nexovue_platform.checks.task import (
    RUN_CHECK_TASK,
    RUN_NOW_RESULT_KIND,
    CheckDefinition,
    probe_queue,
    run_now_arguments,
    time_limits_for,
)
from nexovue_platform.jobs import ENQUEUE, WAKE, JobRequest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.data.postgres.models import JobResult

# A run nobody picks up within this is dropped rather than run minutes later
# for a person who has stopped waiting.
DISPATCH_EXPIRY_SECONDS: Final = 60


class ProbeBroker(Protocol):
    async def dispatch(
        self, definition: CheckDefinition, *, location: str, request_id: str
    ) -> None: ...

    async def collect(self, *, org_id: str, request_id: str) -> RunEvidence | None: ...


class JobsProbeBroker:
    """The scheduler's job queue, reached the way beat reaches it."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def dispatch(
        self, definition: CheckDefinition, *, location: str, request_id: str
    ) -> None:
        await add_job(
            self._session_factory,
            JobRequest(
                task=RUN_CHECK_TASK,
                queue=probe_queue(location),
                kwargs=run_now_arguments(definition, location, request_id),
                org_id=definition.org_id,
                expires_seconds=DISPATCH_EXPIRY_SECONDS,
                **time_limits_for(definition.config),
            ),
        )

    async def collect(self, *, org_id: str, request_id: str) -> RunEvidence | None:
        payload = await collect_result(
            self._session_factory, kind=RUN_NOW_RESULT_KIND, org_id=org_id, request_id=request_id
        )
        return None if payload is None else RunEvidence.model_validate(payload)


async def add_job(session_factory: async_sessionmaker[AsyncSession], request: JobRequest) -> None:
    """Add a job somebody is waiting on, and wake the workers for it."""
    async with session_factory.begin() as db:
        await db.execute(text(ENQUEUE), request.parameters())
        await db.execute(text(WAKE), request.wake_parameters())


async def collect_result(
    session_factory: async_sessionmaker[AsyncSession], *, kind: str, org_id: str, request_id: str
) -> dict[str, object] | None:
    """A job's result, if it has arrived — and only for the organization that asked.

    A request id that is not a UUID was never issued, so it finds nothing.
    """
    try:
        request = uuid.UUID(request_id)
        organization = uuid.UUID(org_id)
    except ValueError:
        return None
    async with session_factory() as db:
        payload: dict[str, object] | None = await db.scalar(
            select(JobResult.payload).where(
                JobResult.request_id == request,
                JobResult.org_id == organization,
                JobResult.kind == kind,
            )
        )
    return payload
