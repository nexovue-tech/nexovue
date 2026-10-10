"""*Send test* (FR3): hand a channel to the deliveries worker and collect the outcome.

The API never contacts a channel's destination itself — outbound requests to
addresses a customer chose stay off the control plane (ADR-011's reason, reused).
The job is added by name, so no scheduler code is imported, and the outcome waits
in `job_results`, found only under the organization that asked.
"""

from __future__ import annotations

from typing import Final, Protocol

from nexovue_platform.alerts.tasks import (
    DELIVERIES_QUEUE,
    SEND_TEST_RESULT_KIND,
    SEND_TEST_TASK,
    send_test_arguments,
)
from nexovue_platform.jobs import JobRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.channels.schemas import SendTestResult
from nexovue_api.checks.run_now import add_job, collect_result

# A test nobody picks up within this is dropped rather than sent minutes later to
# a person who has stopped waiting.
DISPATCH_EXPIRY_SECONDS: Final = 60


class ChannelTester(Protocol):
    async def dispatch(self, *, org_id: str, channel_id: str, request_id: str) -> None: ...

    async def collect(self, *, org_id: str, request_id: str) -> SendTestResult | None: ...


class JobsChannelTester:
    """The scheduler's job queue, reached the way Run now reaches it."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def dispatch(self, *, org_id: str, channel_id: str, request_id: str) -> None:
        await add_job(
            self._session_factory,
            JobRequest(
                task=SEND_TEST_TASK,
                queue=DELIVERIES_QUEUE,
                kwargs=send_test_arguments(
                    org_id=org_id, channel_id=channel_id, request_id=request_id
                ),
                org_id=org_id,
                expires_seconds=DISPATCH_EXPIRY_SECONDS,
            ),
        )

    async def collect(self, *, org_id: str, request_id: str) -> SendTestResult | None:
        outcome = await collect_result(
            self._session_factory, kind=SEND_TEST_RESULT_KIND, org_id=org_id, request_id=request_id
        )
        if outcome is None:
            return None
        status = outcome.get("http_status")
        reason = outcome.get("reason")
        return SendTestResult(
            pending=False,
            delivered=bool(outcome.get("delivered")),
            http_status=status if isinstance(status, int) else None,
            reason=reason if isinstance(reason, str) else None,
        )
