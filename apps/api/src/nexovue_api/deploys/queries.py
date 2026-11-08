"""Deploy reads and writes.

Every statement a signed-in caller reaches carries `org_id` in the statement
rather than in a check afterwards, so another organization's deploy returns
nothing and "not yours" stays indistinguishable from "does not exist".

:func:`live_webhook_secrets` is the one exception, and deliberately so: it is
reached by an unauthenticated caller who has proved nothing yet. The project id
in the path is all it has said; a signature over one of the secrets it returns is
what proves it, and the org_id is then read off the secret that matched, never
off the request. Every write after that binds that org_id.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    DeployEvent,
    DeployIntakeState,
    Project,
    ProjectWebhookSecret,
    Release,
    ReleasePeriod,
    ServiceLastSeen,
)

# ------------------------------------------------------------------- secrets


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
    statement = (
        select(ProjectWebhookSecret)
        .where(
            ProjectWebhookSecret.project_id == project_id,
            ProjectWebhookSecret.revoked_at.is_(None),
        )
        .order_by(ProjectWebhookSecret.created_at, ProjectWebhookSecret.id)
    )
    return (await session.scalars(statement)).all()


async def live_secrets_of(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[ProjectWebhookSecret]:
    """The same list for a signed-in caller, bound to its organization."""
    statement = (
        select(ProjectWebhookSecret)
        .where(
            ProjectWebhookSecret.org_id == org_id,
            ProjectWebhookSecret.project_id == project_id,
            ProjectWebhookSecret.revoked_at.is_(None),
        )
        .order_by(ProjectWebhookSecret.created_at, ProjectWebhookSecret.id)
    )
    return (await session.scalars(statement)).all()


async def lock_project(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Hold the project row until commit, so two mints cannot both see four secrets."""
    await session.execute(
        select(Project.id)
        .where(Project.org_id == org_id, Project.id == project_id)
        .with_for_update()
    )


async def new_row_id(session: AsyncSession) -> uuid.UUID:
    """An id from the same generator as the column's default, read before the insert:
    a sealed secret is bound to its own row's id (design 2)."""
    found = await session.scalar(text("SELECT uuidv7()"))
    if not isinstance(found, uuid.UUID):
        raise LookupError("uuidv7() returned no id")
    return found


def add_webhook_secret(session: AsyncSession, secret: ProjectWebhookSecret) -> None:
    """Stage a new secret. The caller's transaction commits it."""
    session.add(secret)


async def revoke_webhook_secret(
    session: AsyncSession, *, org_id: uuid.UUID, secret_id: uuid.UUID
) -> uuid.UUID | None:
    """Mark one secret withdrawn, answering with its project, or None if there was none.

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
        .returning(ProjectWebhookSecret.project_id)
    )
    found: uuid.UUID | None = await session.scalar(statement)
    return found


async def mark_secret_used(
    session: AsyncSession, *, org_id: uuid.UUID, secret_id: uuid.UUID
) -> None:
    """The secret signed a request just now: what tells a rotation it is safe to withdraw
    the old one (design, *Admin rotating*)."""
    await session.execute(
        update(ProjectWebhookSecret)
        .where(ProjectWebhookSecret.org_id == org_id, ProjectWebhookSecret.id == secret_id)
        .values(last_used_at=func.now())
    )


# -------------------------------------------------------------------- intake


def _intake_upsert(org_id: uuid.UUID, project_id: uuid.UUID, values: Mapping[str, Any]) -> Any:
    return (
        insert(DeployIntakeState)
        .values(org_id=org_id, project_id=project_id, **values)
        .on_conflict_do_update(
            index_elements=[DeployIntakeState.project_id],
            set_=dict(values),
            where=DeployIntakeState.org_id == org_id,
        )
    )


async def record_check(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """A dry run arrived and passed (FR11)."""
    await session.execute(_intake_upsert(org_id, project_id, {"last_check_at": func.now()}))


async def record_deploy_arrival(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    await session.execute(_intake_upsert(org_id, project_id, {"last_deploy_at": func.now()}))


async def record_refusal(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    position: int | None,
    field: str | None,
    secret_id: uuid.UUID,
) -> None:
    """A signed body was refused (FR10, D22): the sender proved the secret."""
    await session.execute(
        _intake_upsert(
            org_id,
            project_id,
            {
                "last_refusal_at": func.now(),
                "last_refusal_position": position,
                "last_refusal_field": field,
                "last_refusal_secret_id": secret_id,
            },
        )
    )


async def intake_state(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> DeployIntakeState | None:
    found: DeployIntakeState | None = await session.scalar(
        select(DeployIntakeState).where(
            DeployIntakeState.org_id == org_id, DeployIntakeState.project_id == project_id
        )
    )
    return found


# -------------------------------------------------------------- the answers


async def known_services(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, services: Sequence[str]
) -> set[str]:
    """Which of these services have sent this project telemetry (FR12's `known`)."""
    rows = await session.scalars(
        select(ServiceLastSeen.service_name).where(
            ServiceLastSeen.org_id == org_id,
            ServiceLastSeen.project_id == project_id,
            ServiceLastSeen.service_name.in_(services),
        )
    )
    return set(rows.all())


async def seen_versions(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, services: Sequence[str]
) -> dict[str, list[str]]:
    """Each service's versions counted in traffic, most recently seen first."""
    rows = await session.execute(
        select(Release.service_name, Release.version)
        .where(
            Release.org_id == org_id,
            Release.project_id == project_id,
            Release.service_name.in_(services),
            Release.last_seen_at.is_not(None),
        )
        .order_by(Release.service_name, Release.last_seen_at.desc(), Release.version)
    )
    found: dict[str, list[str]] = {}
    for service_name, version in rows:
        found.setdefault(service_name, []).append(version)
    return found


async def current_versions(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[Any]:
    """Per service, the counted version whose period in service started last, with when
    it was first seen: the Deploys section's list, where a fixed value stands out."""
    statement = (
        select(Release.service_name, Release.version, Release.first_seen_at)
        .join(
            ReleasePeriod,
            (ReleasePeriod.org_id == Release.org_id) & (ReleasePeriod.release_id == Release.id),
        )
        .where(
            Release.org_id == org_id,
            Release.project_id == project_id,
            Release.first_seen_at.is_not(None),
            ReleasePeriod.ended_at.is_(None),
        )
        .distinct(Release.service_name)
        .order_by(Release.service_name, ReleasePeriod.started_at.desc(), Release.version)
    )
    return (await session.execute(statement)).all()


# ------------------------------------------------------------------- deploys


async def recorded_deliveries(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    delivery_ids: Sequence[str],
) -> dict[str, DeployEvent]:
    """The deploys these delivery ids already stored for this project (FR7)."""
    rows = await session.scalars(
        select(DeployEvent).where(
            DeployEvent.org_id == org_id,
            DeployEvent.project_id == project_id,
            DeployEvent.delivery_id.in_(delivery_ids),
        )
    )
    return {row.delivery_id: row for row in rows}


async def insert_deploy(session: AsyncSession, values: Mapping[str, Any]) -> uuid.UUID | None:
    """Store one deploy, or nothing if its delivery id is already stored: the unique key
    decides a race, so a repeat never reaches its release (FR7, AC10)."""
    statement = (
        insert(DeployEvent)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[DeployEvent.project_id, DeployEvent.delivery_id])
        .returning(DeployEvent.id)
    )
    found: uuid.UUID | None = await session.scalar(statement)
    return found


async def file_deploy(
    session: AsyncSession, *, org_id: uuid.UUID, deploy_id: uuid.UUID, release_id: uuid.UUID
) -> None:
    await session.execute(
        update(DeployEvent)
        .where(DeployEvent.org_id == org_id, DeployEvent.id == deploy_id)
        .values(release_id=release_id)
    )


async def find_deploy(
    session: AsyncSession, *, org_id: uuid.UUID, deploy_id: uuid.UUID
) -> DeployEvent | None:
    found: DeployEvent | None = await session.scalar(
        select(DeployEvent).where(DeployEvent.org_id == org_id, DeployEvent.id == deploy_id)
    )
    return found


async def delete_deploys(
    session: AsyncSession, *, org_id: uuid.UUID, deploy_ids: Sequence[uuid.UUID]
) -> Sequence[Any]:
    """Remove these deploys, answering with each one's release and service."""
    statement = (
        delete(DeployEvent)
        .where(DeployEvent.org_id == org_id, DeployEvent.id.in_(deploy_ids))
        .returning(DeployEvent.id, DeployEvent.release_id, DeployEvent.service_name)
    )
    return (await session.execute(statement)).all()


async def deploys_signed_since(
    session: AsyncSession, *, org_id: uuid.UUID, secret_id: uuid.UUID, since: datetime
) -> list[uuid.UUID]:
    """What one secret signed from a time on, by arrival: arrival is ours, and a stolen
    secret can claim any `deployed_at` it likes inside the bounds (D37)."""
    rows = await session.scalars(
        select(DeployEvent.id).where(
            DeployEvent.org_id == org_id,
            DeployEvent.webhook_secret_id == secret_id,
            DeployEvent.created_at >= since,
        )
    )
    return list(rows.all())


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
