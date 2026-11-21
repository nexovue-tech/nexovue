"""The evidence job's own facts read, pointed at a finding that need not exist (plan T2.15, T2.16).

The bed reads a healthy window, a forged outage or a fault's finding with the same `read_facts` the
`evidence` job runs, so a rule is scored on what it would really have read. It builds a
`FindingRow` for the window and reads Postgres and the telemetry store as the job does; it writes
nothing, and the row it builds is never inserted.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from forged import Observe, Observed, Spec
from healthy import FactsReader
from nexovue_platform.deployment import SELF_HOSTED, Deployment
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.evidence import reads, rules
from nexovue_scheduler.evidence.gather import read_facts
from nexovue_scheduler.evidence.rules import Facts, FindingInput
from nexovue_scheduler.evidence.store import EvidenceStore

# How long after its start a finding is read: the settled read's delay (FR2), when deploy
# records have had time to arrive.
SETTLED_AFTER: Final = timedelta(seconds=300)
# In the outage arm the service is already unhealthy, so its finding began before the export.
OUTAGE_LOOK_BACK: Final = timedelta(minutes=30)
SIGNALS: Final = ("slower", "failing", "traffic")
_FINDINGS_ON_SERVICE: Final = text(
    """
    SELECT id, started_at, signals FROM findings
    WHERE org_id = :org_id AND project_id = :project_id AND service_name = :service
      AND started_at >= :from AND (closed_at IS NULL OR closed_at >= :at)
    ORDER BY started_at LIMIT 1
    """
)


def live_reader(
    engine: AsyncEngine,
    store: EvidenceStore,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    timezone: str = "UTC",
    deployment: Deployment = SELF_HOSTED,
    now: datetime | None = None,
) -> FactsReader:
    """A `FactsReader` over one project's real stores. `now` is the clock the facts are read at;
    left out, each read is taken five minutes after the finding's start, as the settled read is."""

    async def read(finding: FindingInput) -> Facts:
        row = reads.FindingRow(
            id=uuid.uuid5(uuid.NAMESPACE_URL, finding.id),
            org_id=org_id,
            project_id=project_id,
            scope=finding.scope,
            service_name=finding.service,
            severity="warning",
            state="open",
            muted=False,
            flapping=False,
            signals=[{"signal": signal, "key": signal} for signal in finding.signals],
            services=list(finding.services),
            started_at=finding.started_at,
            closed_at=None,
            timezone=timezone,
            project_deleted=False,
        )
        at = now or finding.started_at.astimezone(UTC) + SETTLED_AFTER
        async with engine.connect() as connection, engine.connect() as cache:
            await connection.begin()
            facts = await read_facts(
                connection, store, row, deployment=deployment, now=at, cache=cache
            )
            await connection.rollback()
            await cache.rollback()
        return facts

    return read


def finding_observer(
    engine: AsyncEngine,
    reader: FactsReader,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    wait: int,
    poll_seconds: float = 10.0,
) -> Observe:
    """Watches a project's findings after a forged export: whether one opened on the service
    (or was already open, in the outage arm), and the cause the evidence job's own read gives it."""

    async def observe(spec: Spec) -> Observed:
        looking_from = spec.at - (OUTAGE_LOOK_BACK if spec.outage else timedelta(0))
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        _FINDINGS_ON_SERVICE,
                        {
                            "org_id": org_id,
                            "project_id": project_id,
                            "service": spec.service,
                            "from": looking_from,
                            "at": spec.at,
                        },
                    )
                ).first()
            if row is not None:
                signals = tuple(
                    each["signal"] for each in (row.signals or []) if each.get("signal") in SIGNALS
                )
                facts = await reader(
                    FindingInput(
                        id=str(row.id),
                        scope="service",
                        service=spec.service,
                        started_at=row.started_at,
                        signals=signals or ("slower",),
                    )
                )
                return Observed(True, rules.decide(facts))
            if asyncio.get_running_loop().time() >= deadline:
                return Observed(False, None)
            await asyncio.sleep(poll_seconds)

    return observe
