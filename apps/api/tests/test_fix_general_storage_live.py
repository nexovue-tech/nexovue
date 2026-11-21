"""`finding_fixes` as migration 0077 built it, against real Postgres (F5 plan T2b.1; spec
FR38, BR8; AC75's storage half).

The database holds what rung 2b promises, whoever writes: a general row's language from
FR33's list; an answer stored only at `proposed`, under 8 KiB; a reason only on a row that
ended short; one suggestion in flight per finding (BR8); the row going with its finding; the
requester's id cleared when they are removed, the row kept. Every row is this test's own,
inside a transaction rolled back at the end.

This suite fails rather than skips when Postgres is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
AT: Final = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_general_storage_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _tenant(db: AsyncConnection) -> dict[str, uuid.UUID]:
    org, project, user, finding = (uuid.uuid4() for _ in range(4))
    await db.execute(
        text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Fixes')"),
        {"id": org, "slug": f"fixes-{org.hex[:12]}"},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, org_id, email, role, password_hash) "
            "VALUES (:id, :org, :email, 'member', 'x')"
        ),
        {"id": user, "org": org, "email": f"m-{user.hex[:8]}@example.test"},
    )
    await db.execute(
        text("INSERT INTO projects (id, org_id, slug, name) VALUES (:id, :org, 'prod', 'Prod')"),
        {"id": project, "org": org},
    )
    await db.execute(
        text(
            """
            INSERT INTO findings (id, org_id, project_id, scope, service_name, severity, state,
                                  title, signals, started_at)
            VALUES (:id, :org, :project, 'service', 'checkout', 'warning', 'open', 't',
                    '[]', :at)
            """
        ),
        {"id": finding, "org": org, "project": project, "at": AT},
    )
    return {"org": org, "project": project, "user": user, "finding": finding}


async def _row(db: AsyncConnection, tenant: dict[str, uuid.UUID], **columns: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "kind": "general",
        "explanation_version": 1,
        "state": "proposing",
        "general_language": "python",
        "general_suggestion": None,
        "reason": None,
        **columns,
    }
    suggestion = values.pop("general_suggestion")
    made = await db.scalar(
        text(
            """
            INSERT INTO finding_fixes (org_id, project_id, finding_id, kind, explanation_version,
                                       state, general_language, general_suggestion, reason,
                                       requested_by)
            VALUES (:org, :project, :finding, :kind, :explanation_version, :state,
                    :general_language, CAST(:suggestion AS jsonb), :reason, :user)
            RETURNING id
            """
        ),
        {
            "org": tenant["org"],
            "project": tenant["project"],
            "finding": tenant["finding"],
            "user": tenant["user"],
            "suggestion": None if suggestion is None else json.dumps(suggestion),
            **values,
        },
    )
    assert isinstance(made, uuid.UUID)
    return made


async def _refused(db: AsyncConnection, tenant: dict[str, uuid.UUID], **columns: Any) -> str:
    async with db.begin_nested() as savepoint:
        try:
            await _row(db, tenant, **columns)
        except IntegrityError as refused:
            await savepoint.rollback()
            return str(refused.orig)
    raise AssertionError(f"the database took {columns}")


async def test_a_general_row_as_the_click_leaves_it_is_taken(db: AsyncConnection) -> None:
    tenant = await _tenant(db)

    made = await _row(db, tenant)

    state = await db.scalar(text("SELECT state FROM finding_fixes WHERE id = :id"), {"id": made})
    assert state == "proposing"


@pytest.mark.parametrize(
    ("label", "columns", "constraint"),
    [
        ("a kind not yet built", {"kind": "source", "general_language": None}, "kind_is_known"),
        ("a language off FR33's list", {"general_language": "cobol"}, "general_language"),
        ("a general row with no language", {"general_language": None}, "general_language"),
        (
            "an answer before it passed",
            {"general_suggestion": {"summary": "x"}},
            "general_suggestion_only_when_proposed",
        ),
        (
            "a proposed row with no answer",
            {"state": "proposed"},
            "general_suggestion_only_when_proposed",
        ),
        (
            "an answer over 8 KiB",
            {"state": "proposed", "general_suggestion": {"summary": "x" * 9000}},
            "general_suggestion_is_small",
        ),
        ("a refusal with no reason", {"state": "refused"}, "reason_only_when_it_ended_short"),
        (
            "a reason on a row in flight",
            {"reason": "timed_out"},
            "reason_only_when_it_ended_short",
        ),
        ("a reason off the list", {"state": "failed", "reason": "oops"}, "reason_is_known"),
        ("a state off the list", {"state": "stale"}, "state_is_known"),
    ],
)
async def test_the_database_refuses_what_rung_2b_never_stores(
    db: AsyncConnection, label: str, columns: dict[str, Any], constraint: str
) -> None:
    tenant = await _tenant(db)

    assert constraint in await _refused(db, tenant, **columns), label


async def test_br8_one_suggestion_in_flight_per_finding(db: AsyncConnection) -> None:
    tenant = await _tenant(db)
    await _row(db, tenant)
    await _row(db, tenant, state="failed", reason="timed_out")

    assert "uq_finding_fixes_one_in_flight" in await _refused(db, tenant)


async def test_ac75_deleting_the_finding_deletes_its_suggestions(db: AsyncConnection) -> None:
    tenant = await _tenant(db)
    made = await _row(db, tenant)

    await db.execute(text("DELETE FROM findings WHERE id = :id"), {"id": tenant["finding"]})

    assert (
        await db.scalar(text("SELECT count(*) FROM finding_fixes WHERE id = :id"), {"id": made})
        == 0
    )


async def test_removing_the_requester_keeps_the_row_and_clears_their_id(
    db: AsyncConnection,
) -> None:
    tenant = await _tenant(db)
    made = await _row(db, tenant)

    await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": tenant["user"]})

    kept = await db.scalar(
        text("SELECT requested_by IS NULL FROM finding_fixes WHERE id = :id"), {"id": made}
    )
    assert kept is True


async def test_another_organization_s_finding_cannot_hold_a_suggestion(
    db: AsyncConnection,
) -> None:
    tenant = await _tenant(db)
    other = await _tenant(db)

    refused = await _refused(db, {**tenant, "finding": other["finding"]})

    assert "fk_finding_fixes_finding" in refused
