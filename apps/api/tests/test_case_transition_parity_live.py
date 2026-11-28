"""A case ended by a person, by the tracker sync and by the sweep writes the same rows
apart from who did it (F22 plan T4.6; AC103, AC57), against real Postgres.

The three writers are the API's `findings/cases.py`, and the scheduler's
`case_endings.end_case`, which `connections/sync.py` runs for `tracker_closed` and
`cases.py` for `no_activity`. Each executes the shared statements in
`nexovue_platform.cases`. Compared here: the case's columns, the open joined finding
leaving the case in that transaction (AC57), the timeline's kinds and details, and the
audit row, with only the actor and the resolution allowed to differ. The sync's reading
of the tracker is `test_connections_sync_live.py`'s; this holds what it writes.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api.data.postgres.models import User
from nexovue_api.findings import cases
from nexovue_scheduler import cases as sweep
from nexovue_scheduler.case_endings import end_case

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
NOW: Final = datetime(2026, 11, 2, 14, 2, tzinfo=UTC)
LONG_AGO: Final = NOW - timedelta(days=91)


def _reachable() -> bool:
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(POSTGRES_DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_case_transition_parity_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    made = create_async_engine(ASYNC_DSN)
    try:
        yield made
    finally:
        await made.dispose()


@pytest.fixture
async def org(engine: AsyncEngine) -> AsyncIterator[dict[str, uuid.UUID]]:
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Parity')"),
            {"id": org_id, "slug": f"parity-{org_id.hex[:12]}"},
        )
        owner = (
            await connection.execute(
                text(
                    "INSERT INTO users (org_id, email, role, password_hash) VALUES "
                    "(:org, :email, 'owner', 'x') RETURNING id"
                ),
                {"org": org_id, "email": f"owner-{org_id.hex[:12]}@example.test"},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO projects (id, org_id, slug, name) "
                "VALUES (:id, :org, 'production', 'Production')"
            ),
            {"id": project_id, "org": org_id},
        )
    try:
        yield {"org_id": org_id, "project_id": project_id, "owner": owner}
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": org_id}
            )


async def _case(
    engine: AsyncEngine, org: dict[str, uuid.UUID], service: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """A recovered root with an active case, and an open finding joined to it."""
    async with engine.begin() as connection:
        root = (
            await connection.execute(
                text(
                    "INSERT INTO findings (org_id, project_id, scope, service_name, severity, "
                    "title, started_at, state, closed_at, closed_reason, case_state, "
                    "case_opened_at, case_activity_at) VALUES (:org, :project, 'service', "
                    ":service, 'warning', 'x', :long_ago, 'closed', :long_ago, 'recovered', "
                    "'investigating', :long_ago, :long_ago) RETURNING id"
                ),
                {
                    **org,
                    "org": org["org_id"],
                    "project": org["project_id"],
                    "service": service,
                    "long_ago": LONG_AGO,
                },
            )
        ).scalar_one()
        joined = (
            await connection.execute(
                text(
                    "INSERT INTO findings (org_id, project_id, scope, service_name, severity, "
                    "title, started_at, state, root_finding_id) VALUES (:org, :project, "
                    "'service', :service, 'critical', 'x', :at, 'open', :root) RETURNING id"
                ),
                {
                    "org": org["org_id"],
                    "project": org["project_id"],
                    "service": service,
                    "at": NOW - timedelta(hours=1),
                    "root": root,
                },
            )
        ).scalar_one()
    return root, joined


async def _written(
    engine: AsyncEngine, org: dict[str, uuid.UUID], root: uuid.UUID, joined: uuid.UUID
) -> dict[str, Any]:
    async with engine.connect() as connection:
        case = (
            await connection.execute(
                text(
                    "SELECT case_state, case_resolution, case_resolved_at, case_resolved_by, "
                    "case_warned_at FROM findings WHERE org_id = :org AND id = :id"
                ),
                {"org": org["org_id"], "id": root},
            )
        ).one()
        left = await connection.scalar(
            text("SELECT root_finding_id FROM findings WHERE org_id = :org AND id = :id"),
            {"org": org["org_id"], "id": joined},
        )
        events = (
            await connection.execute(
                text(
                    "SELECT kind, actor_kind, actor_id, detail FROM finding_events "
                    "WHERE org_id = :org AND finding_id = :id ORDER BY at, id"
                ),
                {"org": org["org_id"], "id": root},
            )
        ).all()
        audit = (
            await connection.execute(
                text(
                    "SELECT action, actor_user_id, detail FROM audit_log "
                    "WHERE org_id = :org AND target = :target"
                ),
                {"org": org["org_id"], "target": f"finding:{root}"},
            )
        ).all()
    return {"case": case, "left": left, "events": events, "audit": audit}


def _shape(written: dict[str, Any]) -> dict[str, Any]:
    """What must be equal across writers: everything but the actor and the resolution."""
    case = written["case"]
    return {
        "case": (case.case_state, case.case_resolved_at, case.case_warned_at),
        "left": written["left"],
        "events": [(event.kind, sorted(event.detail)) for event in written["events"]],
        "audit": [(row.action, sorted(row.detail)) for row in written["audit"]],
    }


async def test_a_person_the_sync_and_the_sweep_end_a_case_with_the_same_rows(
    engine: AsyncEngine, org: dict[str, uuid.UUID]
) -> None:
    by_person = await _case(engine, org, "checkout")
    by_sync = await _case(engine, org, "payments")
    by_sweep = await _case(engine, org, "search")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        actor = await session.get(User, org["owner"])
        assert actor is not None
        await cases.resolve(session, actor=actor, finding_id=by_person[0], now=NOW)
    async with engine.begin() as connection:
        assert await end_case(
            connection,
            org_id=org["org_id"],
            root_id=by_sync[0],
            resolution="tracker_closed",
            at=NOW,
            actor_kind="tracker",
        )
    outcome = await sweep.sweep(engine, now=NOW)
    assert outcome.ended_idle >= 1

    person = await _written(engine, org, *by_person)
    tracker = await _written(engine, org, *by_sync)
    silence = await _written(engine, org, *by_sweep)

    assert _shape(person) == _shape(tracker) == _shape(silence)
    # AC57: the open joined finding left the case in that same transaction, with its event.
    for written, (_, joined) in ((person, by_person), (tracker, by_sync), (silence, by_sweep)):
        assert written["left"] is None
        assert written["events"][0].detail == {"left_finding_id": str(joined)}
    assert [event.kind for event in person["events"]] == ["finding_left_case", "case_resolved"]
    # Only who did it, and how it ended, differ.
    assert [case["case"].case_resolution for case in (person, tracker, silence)] == [
        "resolved",
        "tracker_closed",
        "no_activity",
    ]
    assert person["case"].case_resolved_by == org["owner"]
    assert tracker["case"].case_resolved_by is None and silence["case"].case_resolved_by is None
    assert {event.actor_kind for event in person["events"]} == {"person"}
    assert {event.actor_kind for event in tracker["events"]} == {"tracker"}
    assert {event.actor_kind for event in silence["events"]} == {"system"}
    assert [row.actor_user_id for row in person["audit"]] == [org["owner"]]
    assert [row.actor_user_id for row in tracker["audit"] + silence["audit"]] == [None, None]
