"""When each service-condition sweep last ran — so the Alerts screen can say
evaluation is behind, rather than show a quiet list that means nothing (spec,
*Error cases*). The scheduler writes `alert_evaluation_status` as each finishes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from nexovue_platform.alerts.tasks import EVALUATIONS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.alerts.schemas import EvaluationStatus, SweepStatus
from nexovue_api.data.postgres.models import AlertEvaluationStatus

# Past this, the Alerts screen says evaluation is behind (spec, *Error cases*).
BEHIND_AFTER: Final = timedelta(minutes=3)


class EvaluationReader(Protocol):
    async def status(self) -> EvaluationStatus: ...


class PostgresEvaluationReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def status(self) -> EvaluationStatus:
        now = datetime.now(UTC)
        async with self._session_factory() as db:
            recorded = {
                row.evaluation: row for row in await db.scalars(select(AlertEvaluationStatus))
            }
        sweeps = []
        for evaluation in EVALUATIONS:
            row = recorded.get(evaluation)
            if row is None:
                sweeps.append(
                    SweepStatus(name=evaluation, finished_at=None, skipped=None, behind=True)
                )
                continue
            sweeps.append(
                SweepStatus(
                    name=evaluation,
                    finished_at=row.finished_at,
                    skipped=row.skipped,
                    behind=now - row.finished_at > BEHIND_AFTER or row.skipped is not None,
                )
            )
        return EvaluationStatus(sweeps=sweeps)
