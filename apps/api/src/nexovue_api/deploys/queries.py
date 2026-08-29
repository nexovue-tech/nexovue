"""Deploy reads and writes.

Every statement a signed-in caller reaches carries `org_id` in the statement
rather than in a check afterwards, so another organization's deploy returns
nothing and "not yours" stays indistinguishable from "does not exist".

:func:`webhook_keys` is the one exception, and deliberately so: it is reached by
an unauthenticated caller who has no organization yet. It resolves tenancy *from
the project row* — the org_id it returns is the project's own, never a value the
request supplied — and everything after it is scoped by what it found.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import ApiKey, DeployEvent, Project


@dataclass(frozen=True, slots=True)
class WebhookKey:
    """A live ingest key, reduced to what deriving a webhook secret needs."""

    org_id: uuid.UUID
    key_hash: str


async def webhook_keys(session: AsyncSession, *, project_id: uuid.UUID) -> Sequence[WebhookKey]:
    """Every live ingest key on a live project, with the project's tenancy.

    Empty for a project that does not exist, is deleted, or has no live key —
    all three then produce the same refusal as a bad signature, so the endpoint
    cannot be used to find out which project ids are real.
    """
    statement = (
        select(ApiKey.org_id, ApiKey.key_hash)
        .join(Project, Project.id == ApiKey.project_id)
        .where(
            ApiKey.project_id == project_id,
            # The two tenancy columns must agree; a row where they do not is
            # corrupt and must not be the thing that authorizes a write.
            ApiKey.org_id == Project.org_id,
            ApiKey.revoked_at.is_(None),
            Project.deleted_at.is_(None),
        )
        .order_by(ApiKey.created_at, ApiKey.id)
    )
    rows = (await session.execute(statement)).all()
    return [WebhookKey(org_id=row.org_id, key_hash=row.key_hash) for row in rows]


async def live_keys(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[ApiKey]:
    """This project's un-revoked keys, for showing the secrets derived from them."""
    statement = (
        select(ApiKey)
        .where(
            ApiKey.org_id == org_id,
            ApiKey.project_id == project_id,
            ApiKey.revoked_at.is_(None),
        )
        .order_by(ApiKey.created_at, ApiKey.id)
    )
    return (await session.scalars(statement)).all()


async def list_deploy_events(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> Sequence[DeployEvent]:
    """This project's deploys, most recent first — the order a console reads them."""
    statement = (
        select(DeployEvent)
        .where(DeployEvent.org_id == org_id, DeployEvent.project_id == project_id)
        .order_by(DeployEvent.deployed_at.desc(), DeployEvent.id.desc())
        .limit(limit)
    )
    return (await session.scalars(statement)).all()


def add_deploy_event(session: AsyncSession, event: DeployEvent) -> None:
    """Stage a deploy. The caller's transaction commits it."""
    session.add(event)
