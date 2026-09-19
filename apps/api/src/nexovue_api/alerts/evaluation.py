"""When each service-condition sweep last ran — so the Alerts screen can say
evaluation is behind, rather than show a quiet list that means nothing (spec,
*Error cases*). The scheduler leaves this in its broker's Redis."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from nexovue_platform.alerts.tasks import EVALUATIONS, evaluation_status_key
from redis.asyncio import Redis

from nexovue_api.alerts.schemas import EvaluationStatus, SweepStatus

# Past this, the Alerts screen says evaluation is behind (spec, *Error cases*).
BEHIND_AFTER: Final = timedelta(minutes=3)


class EvaluationReader(Protocol):
    async def status(self) -> EvaluationStatus: ...

    async def close(self) -> None: ...


class RedisEvaluationReader:
    def __init__(self, broker_url: str) -> None:
        self._redis = Redis.from_url(broker_url, decode_responses=True)

    async def status(self) -> EvaluationStatus:
        now = datetime.now(UTC)
        sweeps = []
        for evaluation in EVALUATIONS:
            stored = await self._redis.get(evaluation_status_key(evaluation))
            if stored is None:
                sweeps.append(
                    SweepStatus(name=evaluation, finished_at=None, skipped=None, behind=True)
                )
                continue
            record = json.loads(stored)
            finished = datetime.fromisoformat(record["finished_at"])
            sweeps.append(
                SweepStatus(
                    name=evaluation,
                    finished_at=finished,
                    skipped=record.get("skipped"),
                    behind=now - finished > BEHIND_AFTER or record.get("skipped") is not None,
                )
            )
        return EvaluationStatus(sweeps=sweeps)

    async def close(self) -> None:
        await self._redis.aclose()
