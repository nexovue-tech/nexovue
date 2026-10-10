"""What a task leaves for someone else to read: a Run now's or a send test's
result for the API, and when each alert sweep last finished."""

from __future__ import annotations

import json
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_STORE_RESULT: Final = text(
    """
    INSERT INTO job_results (request_id, org_id, kind, payload, expires_at)
    VALUES (
        CAST(:request_id AS uuid), CAST(:org_id AS uuid), :kind, CAST(:payload AS jsonb),
        now() + make_interval(secs => CAST(:ttl_seconds AS double precision))
    )
    ON CONFLICT (request_id) DO NOTHING
    """
)

_RECORD_EVALUATION: Final = text(
    """
    INSERT INTO alert_evaluation_status (evaluation, finished_at, skipped)
    VALUES (:evaluation, now(), :skipped)
    ON CONFLICT (evaluation) DO UPDATE
    SET finished_at = EXCLUDED.finished_at, skipped = EXCLUDED.skipped
    """
)


async def store(
    engine: AsyncEngine,
    *,
    kind: str,
    org_id: str,
    request_id: str,
    payload: dict[str, Any],
    ttl_seconds: int,
) -> None:
    """Leave a result under its request id, for the organization that asked.

    The first result stands: a job run twice — a lease that lapsed — must not
    replace what a person may already have read.
    """

    async with engine.begin() as connection:
        await connection.execute(
            _STORE_RESULT,
            {
                "request_id": request_id,
                "org_id": org_id,
                "kind": kind,
                "payload": json.dumps(payload),
                "ttl_seconds": ttl_seconds,
            },
        )


async def record_evaluation(engine: AsyncEngine, evaluation: str, *, skipped: str | None) -> None:
    """When an alert sweep finished, and why it skipped if it did."""
    async with engine.begin() as connection:
        await connection.execute(_RECORD_EVALUATION, {"evaluation": evaluation, "skipped": skipped})
