"""Dispatching a run and waiting, briefly, for what it saw.

Nothing a Run now does is recorded: the worker publishes no measurement and
writes no state (AC24). The result waits in `job_results` and is read only for
the organization that asked, so another tenant's request id reads nothing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Final

from nexovue_platform.checks.task import new_run_id
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.checks import service as checks
from nexovue_api.checks.run_now import ProbeBroker
from nexovue_api.checks.schemas import RunNowAccepted, RunNowRequest, RunNowResult
from nexovue_api.errors.exceptions import DependencyUnavailableError

POLL_INTERVAL_SECONDS: Final = 0.5
MAX_WAIT_SECONDS: Final = 25

_UNAVAILABLE: Final = "Run now is not available right now: the probe queue cannot be reached."


async def dispatch(
    session: AsyncSession,
    probes: ProbeBroker,
    *,
    org_id: uuid.UUID,
    request: RunNowRequest,
    available_locations: list[str],
) -> RunNowAccepted:
    location = request.location or available_locations[0]
    definition = await checks.run_now_definition(
        session,
        org_id=org_id,
        project_id=request.project_id,
        name=request.name,
        config=request.config,
        degraded_ms=request.degraded_ms,
        failing_ms=request.failing_ms,
        location=location,
        available_locations=available_locations,
    )
    request_id = new_run_id()
    try:
        await probes.dispatch(definition, location=location, request_id=request_id)
    except Exception as unreachable:
        raise DependencyUnavailableError(_UNAVAILABLE) from unreachable
    return RunNowAccepted(request_id=request_id, location=location)


async def result(
    probes: ProbeBroker, *, org_id: uuid.UUID, request_id: str, wait_seconds: float
) -> RunNowResult:
    """The result, waiting up to `wait_seconds` for it to arrive.

    Waiting here rather than having the console poll every half second keeps a
    run to one or two requests against the rate limit.
    """
    deadline = time.monotonic() + min(wait_seconds, MAX_WAIT_SECONDS)
    while True:
        try:
            evidence = await probes.collect(org_id=str(org_id), request_id=request_id)
        except Exception as unreachable:
            raise DependencyUnavailableError(_UNAVAILABLE) from unreachable
        if evidence is not None:
            return RunNowResult(request_id=request_id, status="complete", evidence=evidence)
        if time.monotonic() >= deadline:
            return RunNowResult(request_id=request_id, status="pending")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
