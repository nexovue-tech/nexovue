"""*Send test* (FR3): hand a channel to the deliveries worker and collect the outcome.

The API never contacts a channel's destination itself — outbound requests to
addresses a customer chose stay off the control plane (ADR-011's reason, reused).
The task is sent by name, so no scheduler code is imported, and the outcome waits
in Redis under a tenant-first key.
"""

from __future__ import annotations

import json
from typing import Final, Protocol

import anyio
from celery import Celery
from nexovue_platform.alerts.tasks import (
    DELIVERIES_QUEUE,
    SEND_TEST_TASK,
    send_test_arguments,
    send_test_result_key,
)
from redis.asyncio import Redis

from nexovue_api.channels.schemas import SendTestResult

# A test nobody picks up within this is dropped rather than sent minutes later to
# a person who has stopped waiting.
DISPATCH_EXPIRY_SECONDS: Final = 60


class ChannelTester(Protocol):
    async def dispatch(self, *, org_id: str, channel_id: str, request_id: str) -> None: ...

    async def collect(self, *, org_id: str, request_id: str) -> SendTestResult | None: ...

    async def close(self) -> None: ...


class CeleryChannelTester:
    """The scheduler's own broker, reached the way Run now reaches it."""

    def __init__(self, broker_url: str) -> None:
        self._celery = Celery("nexovue-api-channel-tests", broker=broker_url)
        self._celery.conf.update(broker_connection_retry_on_startup=False, result_backend=None)
        self._redis = Redis.from_url(broker_url, decode_responses=True)

    async def dispatch(self, *, org_id: str, channel_id: str, request_id: str) -> None:
        # kombu publishes synchronously; off the event loop, as Run now does.
        await anyio.to_thread.run_sync(
            lambda: self._celery.send_task(
                SEND_TEST_TASK,
                kwargs=send_test_arguments(
                    org_id=org_id, channel_id=channel_id, request_id=request_id
                ),
                queue=DELIVERIES_QUEUE,
                expires=DISPATCH_EXPIRY_SECONDS,
            )
        )

    async def collect(self, *, org_id: str, request_id: str) -> SendTestResult | None:
        stored = await self._redis.get(send_test_result_key(org_id, request_id))
        if stored is None:
            return None
        outcome = json.loads(stored)
        return SendTestResult(
            pending=False,
            delivered=bool(outcome["delivered"]),
            http_status=outcome["http_status"],
            reason=outcome["reason"],
        )

    async def close(self) -> None:
        await self._redis.aclose()
