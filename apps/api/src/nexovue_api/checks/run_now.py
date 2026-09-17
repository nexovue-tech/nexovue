"""Run now: hand a definition to a probe worker and collect what it saw (ADR-011).

The API never makes the request itself — outbound calls to addresses a customer
chose stay off the control plane, beside the database credentials. It sends the
task by name, so no scheduler code is imported, and reads the result the worker
leaves in Redis under a tenant-first key.
"""

from __future__ import annotations

from typing import Final, Protocol

import anyio
from celery import Celery
from nexovue_platform.checks.evidence import RunEvidence
from nexovue_platform.checks.task import (
    RUN_CHECK_TASK,
    CheckDefinition,
    probe_queue,
    run_now_arguments,
    run_now_result_key,
    time_limits_for,
)
from redis.asyncio import Redis

# A task nobody picks up within this is dropped rather than run minutes later
# for a person who has stopped waiting.
DISPATCH_EXPIRY_SECONDS: Final = 60


class ProbeBroker(Protocol):
    async def dispatch(
        self, definition: CheckDefinition, *, location: str, request_id: str
    ) -> None: ...

    async def collect(self, *, org_id: str, request_id: str) -> RunEvidence | None: ...

    async def close(self) -> None: ...


class CeleryProbeBroker:
    """The scheduler's own broker, reached the way beat reaches it."""

    def __init__(self, broker_url: str) -> None:
        self._celery = Celery("nexovue-api", broker=broker_url)
        self._celery.conf.update(broker_connection_retry_on_startup=False, result_backend=None)
        self._redis = Redis.from_url(broker_url, decode_responses=True)

    async def dispatch(
        self, definition: CheckDefinition, *, location: str, request_id: str
    ) -> None:
        arguments = run_now_arguments(definition, location, request_id)
        # kombu publishes synchronously; off the event loop, like boto3 is.
        await anyio.to_thread.run_sync(
            lambda: self._celery.send_task(
                RUN_CHECK_TASK,
                kwargs=arguments,
                queue=probe_queue(location),
                expires=DISPATCH_EXPIRY_SECONDS,
                **time_limits_for(definition.config),
            )
        )

    async def collect(self, *, org_id: str, request_id: str) -> RunEvidence | None:
        stored = await self._redis.get(run_now_result_key(org_id, request_id))
        if stored is None:
            return None
        return RunEvidence.model_validate_json(stored)

    async def close(self) -> None:
        await self._redis.aclose()
