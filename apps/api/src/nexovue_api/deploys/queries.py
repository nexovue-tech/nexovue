"""Deploy reads and writes.

Every statement a signed-in caller reaches carries `org_id` in the statement
rather than in a check afterwards, so another organization's deploy returns
nothing and "not yours" stays indistinguishable from "does not exist".

:func:`live_webhook_secrets` is the one exception, and deliberately so: it is
reached by an unauthenticated caller who has proved nothing yet. The project id
in the path is all it has said; a signature over one of the secrets it returns is
what proves it, and the org_id is then read off the secret that matched, never
off the request.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import ApiKey, DeployEvent, ProjectWebhookSecret


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


async def live_webhook_secrets(
    session: AsyncSession, *, project_id: uuid.UUID
) -> Sequence[ProjectWebhookSecret]:
    """Every secret this project will currently accept a signature from.

    Plural on purpose: a rotation creates the new secret before CI is updated
    and revokes the old one afterwards, so both are live for a while and a
    delivery signed with either has to be accepted.

    Not scoped by org_id, and deliberately so — the caller is an unauthenticated
    webhook that has proved nothing yet. The project id in the path is the only
    thing it has said, and the signature is what proves it. The org is read off
    the secret that matched, never off the request.
    """
    statement = select(ProjectWebhookSecret).where(
        ProjectWebhookSecret.project_id == project_id,
        ProjectWebhookSecret.revoked_at.is_(None),
    )
    return (await session.scalars(statement)).all()


async def delivery_already_recorded(
    session: AsyncSession, *, project_id: uuid.UUID, delivery_id: str
) -> bool:
    """Whether this exact delivery has been stored for this project before.

    The unique index is what actually enforces it; this is the check that turns
    a would-be constraint violation into a clean, quiet answer.
    """
    statement = select(DeployEvent.id).where(
        DeployEvent.project_id == project_id, DeployEvent.delivery_id == delivery_id
    )
    return await session.scalar(statement) is not None


def add_webhook_secret(session: AsyncSession, secret: ProjectWebhookSecret) -> None:
    """Stage a new secret. The caller's transaction commits it."""
    session.add(secret)


async def revoke_webhook_secret(
    session: AsyncSession, *, org_id: uuid.UUID, secret_id: uuid.UUID
) -> bool:
    """Mark one secret withdrawn, reporting whether there was one to withdraw.

    Scoped by organization in the statement, so a revoke aimed at another
    tenant's secret changes nothing rather than needing a check afterwards.
    """
    statement = (
        update(ProjectWebhookSecret)
        .where(
            ProjectWebhookSecret.id == secret_id,
            ProjectWebhookSecret.org_id == org_id,
            ProjectWebhookSecret.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
        .returning(ProjectWebhookSecret.id)
    )
    return await session.scalar(statement) is not None


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


async def deploy_events_between(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    from_time: datetime,
    to_time: datetime,
    limit: int,
) -> Sequence[DeployEvent]:
    """This project's deploys inside a window, most recent first, bounded.

    The bound is the caller's, because only the caller knows what it does when
    the window holds more than it asked for. Nothing here returns "whatever
    matches": how many deploys a window holds is a customer's CI's decision.
    """
    statement = (
        select(DeployEvent)
        .where(
            DeployEvent.org_id == org_id,
            DeployEvent.project_id == project_id,
            DeployEvent.deployed_at >= from_time,
            DeployEvent.deployed_at <= to_time,
        )
        .order_by(DeployEvent.deployed_at.desc(), DeployEvent.id.desc())
        .limit(limit)
    )
    return (await session.scalars(statement)).all()


async def latest_deploy_per_service_before(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, before: datetime
) -> Sequence[DeployEvent]:
    """The most recent deploy of each service strictly before an instant.

    One row per service rather than the whole history behind the window: what
    precedes something is the *last* deploy of its service, and every earlier one
    can answer nothing that this row does not. Without this a question about a
    window would either read a project's entire deploy history or invent a
    lookback nobody decided on.

    `DISTINCT ON` is Postgres's, and the control plane has no other target.
    """
    statement = (
        select(DeployEvent)
        .where(
            DeployEvent.org_id == org_id,
            DeployEvent.project_id == project_id,
            DeployEvent.deployed_at < before,
        )
        .distinct(DeployEvent.service_name)
        .order_by(DeployEvent.service_name, DeployEvent.deployed_at.desc(), DeployEvent.id.desc())
    )
    return (await session.scalars(statement)).all()


def add_deploy_event(session: AsyncSession, event: DeployEvent) -> None:
    """Stage a deploy. The caller's transaction commits it."""
    session.add(event)
