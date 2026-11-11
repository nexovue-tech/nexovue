"""What a connection reaches, as an admin shapes it (F22 plan T6.2): allowing issues in a
public repository and withdrawing that, and which repository each service's issues go to.

**A public repository is refused by default** (BR12, D12). An admin may allow one, and the
console warns first what becomes public; the allowance is per repository (D31), audited,
and may be withdrawn, after which creation stops at the next try, because the job reads
the allowance before every create (AC51). Only a repository that is public now can be
allowed: an allowance given ahead of time would let a repository turn public later with
nobody warned.

**A mapping** names a project's service and one place; the create panel preselects it.
It is optional, and audited as one change whichever way it goes.

Every statement binds the caller's organization, and a place, project or mapping
elsewhere is the same 404 as none at all (AC72). Nothing here calls a tracker (AC32).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.connections import DISCONNECTED, TRACKER_KINDS
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.connections.schemas import (
    Availability,
    ConnectionPlace,
    ServiceRepositoryView,
)
from nexovue_api.data.postgres.models import (
    Connection,
    ConnectionRepository,
    Project,
    ServiceRepository,
)
from nexovue_api.errors.exceptions import ConflictError, NotFoundError

NO_SUCH_PLACE: Final = "No such repository on this organization's connection."
NO_SUCH_PROJECT: Final = "No such project."
NO_SUCH_MAPPING: Final = "This service has no repository set."
PRIVATE_NEEDS_NO_ALLOWANCE: Final = (
    "This repository is private, so there is nothing to allow. If it turns public, issues "
    "there are refused until an admin allows it again."
)


def place_view(place: ConnectionRepository) -> ConnectionPlace:
    return ConnectionPlace(
        id=place.id,
        key=place.key,
        is_private=place.is_private,
        is_reachable=place.is_reachable,
        public_allowed_at=place.public_allowed_at,
    )


async def _locked_place(
    session: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID, place_id: uuid.UUID
) -> ConnectionRepository:
    """A place of this organization's live tracker connection, locked; else the 404."""
    found: ConnectionRepository | None = await session.scalar(
        select(ConnectionRepository)
        .join(
            Connection,
            (Connection.org_id == ConnectionRepository.org_id)
            & (Connection.id == ConnectionRepository.connection_id),
        )
        .where(
            ConnectionRepository.org_id == org_id,
            ConnectionRepository.connection_id == connection_id,
            ConnectionRepository.id == place_id,
            Connection.org_id == org_id,
            Connection.state != DISCONNECTED,
            Connection.kind.in_(TRACKER_KINDS),
        )
        .with_for_update(of=ConnectionRepository)
    )
    if found is None:
        raise NotFoundError(NO_SUCH_PLACE)
    return found


async def set_public_allowed(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    connection_id: uuid.UUID,
    place_id: uuid.UUID,
    allowed: bool,
    availability: Availability,
    now: datetime,
) -> ConnectionPlace:
    """FR42, AC115: allow issues in a public repository, or withdraw that. Asking for
    what already holds changes nothing and writes no second audit row."""
    if availability.refusal is not None:
        raise ConflictError(availability.refusal)
    place = await _locked_place(
        session, org_id=org_id, connection_id=connection_id, place_id=place_id
    )
    if allowed and place.is_private:
        raise ConflictError(PRIVATE_NEEDS_NO_ALLOWANCE)
    if allowed == (place.public_allowed_at is not None):
        return place_view(place)
    place.public_allowed_at = now if allowed else None
    place.public_allowed_by = actor_user_id if allowed else None
    audit.record_place_event(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=audit.CONNECTION_PUBLIC_REPOSITORY_ALLOWED
        if allowed
        else audit.CONNECTION_PUBLIC_REPOSITORY_WITHDRAWN,
        connection_id=connection_id,
        place_id=place.id,
        place_key=place.key,
    )
    await session.flush()
    return place_view(place)


# ------------------------------------------------------------------ mapping


async def mappings(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID | None
) -> list[ServiceRepositoryView]:
    """Every service mapped to a repository of a live tracker connection, by project."""
    statement = (
        select(ServiceRepository, ConnectionRepository.key)
        .join(
            ConnectionRepository,
            (ConnectionRepository.org_id == ServiceRepository.org_id)
            & (ConnectionRepository.id == ServiceRepository.place_id),
        )
        .join(
            Connection,
            (Connection.org_id == ConnectionRepository.org_id)
            & (Connection.id == ConnectionRepository.connection_id),
        )
        .where(
            ServiceRepository.org_id == org_id,
            ConnectionRepository.org_id == org_id,
            Connection.org_id == org_id,
            Connection.state != DISCONNECTED,
        )
        .order_by(ServiceRepository.project_id, ServiceRepository.service_name)
    )
    if project_id is not None:
        statement = statement.where(ServiceRepository.project_id == project_id)
    rows = (await session.execute(statement)).all()
    return [
        ServiceRepositoryView(
            project_id=row.project_id,
            service_name=row.service_name,
            place_id=row.place_id,
            place_key=key,
        )
        for row, key in rows
    ]


async def _project(session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    found = await session.scalar(
        select(Project.id).where(
            Project.org_id == org_id, Project.id == project_id, Project.deleted_at.is_(None)
        )
    )
    if found is None:
        raise NotFoundError(NO_SUCH_PROJECT)


async def _place_of_live_connection(
    session: AsyncSession, *, org_id: uuid.UUID, place_id: uuid.UUID
) -> ConnectionRepository:
    found: ConnectionRepository | None = await session.scalar(
        select(ConnectionRepository)
        .join(
            Connection,
            (Connection.org_id == ConnectionRepository.org_id)
            & (Connection.id == ConnectionRepository.connection_id),
        )
        .where(
            ConnectionRepository.org_id == org_id,
            ConnectionRepository.id == place_id,
            Connection.org_id == org_id,
            Connection.state != DISCONNECTED,
            Connection.kind.in_(TRACKER_KINDS),
        )
    )
    if found is None:
        raise NotFoundError(NO_SUCH_PLACE)
    return found


async def map_service(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    place_id: uuid.UUID,
    now: datetime,
) -> ServiceRepositoryView:
    """Set, or change, which repository a service's issues go to by default."""
    await _project(session, org_id=org_id, project_id=project_id)
    place = await _place_of_live_connection(session, org_id=org_id, place_id=place_id)
    upsert = insert(ServiceRepository).values(
        org_id=org_id,
        project_id=project_id,
        service_name=service_name,
        place_id=place.id,
        changed_at=now,
    )
    await session.execute(
        upsert.on_conflict_do_update(
            index_elements=["org_id", "project_id", "service_name"],
            set_={"place_id": upsert.excluded.place_id, "changed_at": upsert.excluded.changed_at},
        )
    )
    _audit_mapping(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        project_id=project_id,
        service_name=service_name,
        place_id=place.id,
    )
    return ServiceRepositoryView(
        project_id=project_id, service_name=service_name, place_id=place.id, place_key=place.key
    )


async def unmap_service(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
) -> None:
    removed = await session.scalar(
        delete(ServiceRepository)
        .where(
            ServiceRepository.org_id == org_id,
            ServiceRepository.project_id == project_id,
            ServiceRepository.service_name == service_name,
        )
        .returning(ServiceRepository.place_id)
    )
    if removed is None:
        raise NotFoundError(NO_SUCH_MAPPING)
    _audit_mapping(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        project_id=project_id,
        service_name=service_name,
        place_id=None,
    )


def _audit_mapping(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str,
    place_id: uuid.UUID | None,
) -> None:
    audit.record_service_repository_changed(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        project_id=project_id,
        service_name=service_name,
        place_id=place_id,
    )
