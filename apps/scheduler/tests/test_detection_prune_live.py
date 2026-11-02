"""What detection keeps, and for how long (plan T6.3; C24; design revision 2.1,
security m4), against the development Postgres.

The expiry sweep (`sweep:expired-rows`) deletes:

- closed findings once their project's derived retention has passed, since a
  finding keeps telemetry text, its service and endpoint names, and must not
  outlive the telemetry it names;
- the install-wide records, `intake_refusals` and `detection_events`, and the
  `baseline_shifts`, once no normal reads them: after the baseline job's history.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from alert_fixtures import AlertTenant, drop_tenant, make_tenant, postgres_reachable
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.detection.baselines import HISTORY
from nexovue_scheduler.jobs import expiry

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_detection_prune_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

DAY: Final = timedelta(days=1)
# What every normal reads back over (`baselines.project_exclusions`, `project_shifts`).
READ_BACK: Final = HISTORY + DAY
FREE_DERIVED: Final = timedelta(days=30)
PAID_DERIVED: Final = timedelta(days=395)
RECEIVER: Final = f"prune-live-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def paid(engine: AsyncEngine) -> AsyncIterator[AlertTenant]:
    """An organization on a plan that keeps derived data 395 days."""
    made = await make_tenant(engine, with_check=False)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE organizations SET plan = 'pro' WHERE id = :id"), {"id": made.org_id}
        )
    try:
        yield made
    finally:
        await drop_tenant(engine, made)


async def _finding(
    engine: AsyncEngine, tenant: AlertTenant, *, closed_ago: timedelta | None, service: str
) -> uuid.UUID:
    now = datetime.now(UTC)
    started = now - (closed_ago or DAY) - DAY
    async with engine.begin() as connection:
        found: uuid.UUID = (
            await connection.execute(
                text(
                    """
                    INSERT INTO findings
                        (org_id, project_id, scope, service_name, severity, state,
                         closed_reason, title, started_at, closed_at)
                    VALUES (:org_id, :project_id, 'service', :service, 'warning', :state,
                            :reason, :title, :started, :closed)
                    RETURNING id
                    """
                ),
                {
                    "org_id": tenant.org_id,
                    "project_id": tenant.project_id,
                    "service": service,
                    "state": "open" if closed_ago is None else "closed",
                    "reason": None if closed_ago is None else "recovered",
                    "title": f"{service} is twice as slow as normal",
                    "started": started,
                    "closed": None if closed_ago is None else now - closed_ago,
                },
            )
        ).scalar_one()
    return found


async def _remaining(engine: AsyncEngine, table: str, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(f"SELECT id FROM {table} WHERE id = ANY(:ids)"), {"ids": ids}
        )
        return {row.id for row in rows}


async def test_a_closed_finding_is_purged_once_its_project_s_derived_retention_passes(
    engine: AsyncEngine, project: AlertTenant, paid: AlertTenant
) -> None:
    """Security m4: a finding's title and signals name services and endpoints, so a
    closed one goes with the telemetry it names. An open one stays, however old: it
    is still the customer's current problem. A paid plan keeps its 395 days."""
    expired = await _finding(engine, project, closed_ago=FREE_DERIVED + DAY, service="old")
    kept = await _finding(engine, project, closed_ago=FREE_DERIVED - DAY, service="recent")
    still_open = await _finding(engine, project, closed_ago=None, service="ongoing")
    paid_kept = await _finding(engine, paid, closed_ago=FREE_DERIVED + DAY, service="paid")
    paid_expired = await _finding(engine, paid, closed_ago=PAID_DERIVED + DAY, service="paid-old")

    removed = await expiry.sweep(engine)

    assert removed["findings"] >= 2
    remaining = await _remaining(
        engine, "findings", [expired, kept, still_open, paid_kept, paid_expired]
    )
    assert remaining == {kept, still_open, paid_kept}


async def test_install_wide_records_and_shifts_go_once_no_normal_reads_them(
    engine: AsyncEngine, project: AlertTenant
) -> None:
    """C24: `intake_refusals`, `detection_events` and `baseline_shifts` would grow
    without limit. Each is kept as long as the baseline job reads back, which keeps
    refused and held minutes, and shifted days, out of every normal."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    old, recent = now - READ_BACK - DAY, now - READ_BACK + DAY
    async with engine.begin() as connection:
        for minute in (old, recent):
            await connection.execute(
                text(
                    "INSERT INTO intake_refusals (minute, receiver_id, reason, count) "
                    "VALUES (:minute, :receiver, 'broker_unavailable', 5)"
                ),
                {"minute": minute, "receiver": RECEIVER},
            )
        events = [
            (
                await connection.execute(
                    text(
                        "INSERT INTO detection_events (at, kind, detail) "
                        "VALUES (:at, 'prune_probe', CAST(:detail AS jsonb)) RETURNING id"
                    ),
                    {"at": at, "detail": json.dumps({"receiver": RECEIVER})},
                )
            ).scalar_one()
            for at in (old, recent)
        ]
        shifts = [
            (
                await connection.execute(
                    text(
                        """
                        INSERT INTO baseline_shifts
                            (org_id, project_id, service_name, signal, from_minute, ratio,
                             reason, created_at)
                        VALUES (:org_id, :project_id, 'checkout', 'slower', :at, 1.5,
                                'new_normal', :at)
                        RETURNING id
                        """
                    ),
                    {"org_id": project.org_id, "project_id": project.project_id, "at": at},
                )
            ).scalar_one()
            for at in (old, recent)
        ]

    removed = await expiry.sweep(engine)

    assert removed["intake_refusals"] >= 1
    assert removed["detection_events"] >= 1
    assert removed["baseline_shifts"] >= 1
    async with engine.connect() as connection:
        minutes = await connection.execute(
            text("SELECT minute FROM intake_refusals WHERE receiver_id = :receiver"),
            {"receiver": RECEIVER},
        )
        assert [row.minute for row in minutes] == [recent]
        await connection.execute(
            text("DELETE FROM intake_refusals WHERE receiver_id = :receiver"),
            {"receiver": RECEIVER},
        )
    assert await _remaining(engine, "detection_events", events) == {events[1]}
    assert await _remaining(engine, "baseline_shifts", shifts) == {shifts[1]}
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM detection_events WHERE id = ANY(:ids)"), {"ids": events}
        )
