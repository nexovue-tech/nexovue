"""Where each service's code lives (spec FR5b, AC12a, AC12e; design, *`/settings/
connections`: code locations*; plan T3.4; ADR-083).

An admin (`MANAGE_CONNECTIONS`) sets one repository the Fix App reaches and a folder in it,
for the whole organization or one project's override, many services at once. A blank
folder is the whole repository; an optional **path in production** (`/app`) maps what a
stack reports to that folder. **Each row is audited**, since widening a folder must not
pass quietly; a folder outside every live consent stops new source until an owner agrees
(FR8), which `consented` tells the admin at once.

A bulk form saves every row or none, naming the row it refused (AC12a). Every statement
binds the caller's organization; a repository counts only when the organization's own
live Fix App reaches it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.fixes.consent import normal_folder
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.connections.fix_schemas import (
    CodeLocationRemovals,
    CodeLocationRow,
    CodeLocations,
    CodeLocationsRequest,
    CodeLocationView,
)
from nexovue_api.data.postgres.models import (
    ConnectionRepository,
    Project,
    ServiceCodeLocation,
    ServiceLastSeen,
)
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.findings import fix_access

_ROW_REFUSED: Final = "Row {row}: {why}"
_NO_REPOSITORY: Final = "the Nexovue suggestions App does not reach that repository."
_BAD_FOLDER: Final = (
    "a folder is a relative path with no `..`, no leading `/` and no control character."
)
_BAD_PRODUCTION_PATH: Final = "a path in production has no `..` and no control character."
_NO_PROJECT: Final = "no such project."
_TWICE: Final = "this service and project are named twice."
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")


def _production_path(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    path = value.strip().rstrip("/")
    if _CONTROL.search(path) or ".." in path.split("/") or not path:
        raise ValueError("path")
    return path


async def list_locations(db: AsyncSession, *, identity: Identity) -> CodeLocations:
    """Every code location, with whether a live consent covers it now, and the services
    seen with none. A support view reads none (as `GET /connections` gives it none)."""
    if identity.impersonation is not None:
        return CodeLocations(locations=[], services_without_location=[])
    org_id = identity.org_id
    current = fix_access.terms_of(await fix_access.ai_connection(db, org_id=org_id))
    found = (
        await db.execute(
            select(ServiceCodeLocation, ConnectionRepository.key)
            .join(
                ConnectionRepository,
                (ConnectionRepository.org_id == ServiceCodeLocation.org_id)
                & (ConnectionRepository.id == ServiceCodeLocation.connection_repository_id),
            )
            .where(ServiceCodeLocation.org_id == org_id)
            .order_by(ServiceCodeLocation.service_name, ServiceCodeLocation.project_id)
        )
    ).all()
    views = []
    for location, key in found:
        consents = await fix_access.live_consents(
            db, org_id=org_id, repository_id=location.connection_repository_id
        )
        views.append(
            CodeLocationView(
                id=location.id,
                service=location.service_name,
                project_id=location.project_id,
                repository_id=location.connection_repository_id,
                repository=key,
                folder=location.folder,
                path_in_production=location.path_in_production,
                consented=fix_access.covering_consent(consents, location.folder, current)
                is not None,
                set_at=location.set_at,
            )
        )
    located = {view.service for view in views}
    seen = await db.scalars(
        select(ServiceLastSeen.service_name)
        .where(ServiceLastSeen.org_id == org_id)
        .distinct()
        .order_by(ServiceLastSeen.service_name)
    )
    return CodeLocations(
        locations=views,
        services_without_location=[name for name in seen if name not in located],
    )


async def set_locations(
    db: AsyncSession, *, identity: Identity, request: CodeLocationsRequest, now: datetime
) -> CodeLocations:
    """Save every row, or none and name the one refused (AC12a); audit each."""
    org_id = identity.org_id
    checked: list[tuple[CodeLocationRow, str | None, str | None]] = []
    keys: set[tuple[uuid.UUID | None, str]] = set()
    for number, row in enumerate(request.rows, start=1):
        checked.append(await _checked(db, org_id, row, number))
        key = (row.project_id, row.service)
        if key in keys:
            raise InvalidRequestError(_ROW_REFUSED.format(row=number, why=_TWICE), row=number)
        keys.add(key)
    for row, folder, production in checked:
        await _save(db, identity, row, folder, production, now=now)
    await db.commit()
    return await list_locations(db, identity=identity)


async def _checked(
    db: AsyncSession, org_id: uuid.UUID, row: CodeLocationRow, number: int
) -> tuple[CodeLocationRow, str | None, str | None]:
    def refused(why: str) -> InvalidRequestError:
        return InvalidRequestError(_ROW_REFUSED.format(row=number, why=why), row=number)

    if (
        await fix_access.fix_app_repository(db, org_id=org_id, repository_id=row.repository_id)
        is None
    ):
        raise refused(_NO_REPOSITORY)
    if row.project_id is not None:
        project = await db.scalar(
            select(Project.id).where(Project.org_id == org_id, Project.id == row.project_id)
        )
        if project is None:
            raise refused(_NO_PROJECT)
    try:
        folder = normal_folder(row.folder)
    except ValueError as bad:
        raise refused(_BAD_FOLDER) from bad
    try:
        production = _production_path(row.path_in_production)
    except ValueError as bad:
        raise refused(_BAD_PRODUCTION_PATH) from bad
    return row, folder, production


async def _save(
    db: AsyncSession,
    identity: Identity,
    row: CodeLocationRow,
    folder: str | None,
    production: str | None,
    *,
    now: datetime,
) -> None:
    org_id = identity.org_id
    existing = await db.scalar(
        select(ServiceCodeLocation)
        .where(
            ServiceCodeLocation.org_id == org_id,
            ServiceCodeLocation.service_name == row.service,
            ServiceCodeLocation.project_id.is_(None)
            if row.project_id is None
            else ServiceCodeLocation.project_id == row.project_id,
        )
        .with_for_update()
    )
    before = (
        None
        if existing is None
        else {"repository_id": str(existing.connection_repository_id), "folder": existing.folder}
    )
    location = existing or ServiceCodeLocation(
        id=uuid.uuid4(), org_id=org_id, project_id=row.project_id, service_name=row.service
    )
    location.connection_repository_id = row.repository_id
    location.folder = folder
    location.path_in_production = production
    location.set_by = identity.user.id
    location.set_at = now
    if existing is None:
        db.add(location)
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=identity.user.id,
        action=audit.FIX_CODE_LOCATION_CHANGED,
        target_kind=audit.CODE_LOCATION_TARGET,
        target_id=location.id,
        detail={
            "service": row.service,
            "project_id": None if row.project_id is None else str(row.project_id),
            "repository_id": str(row.repository_id),
            "folder": folder,
            "path_in_production": production,
            "before": before,
        },
    )


async def remove_locations(
    db: AsyncSession, *, identity: Identity, request: CodeLocationRemovals
) -> None:
    org_id = identity.org_id
    for row in request.rows:
        removed = await db.execute(
            delete(ServiceCodeLocation)
            .where(
                ServiceCodeLocation.org_id == org_id,
                ServiceCodeLocation.service_name == row.service,
                ServiceCodeLocation.project_id.is_(None)
                if row.project_id is None
                else ServiceCodeLocation.project_id == row.project_id,
            )
            .returning(ServiceCodeLocation.id, ServiceCodeLocation.folder)
        )
        for location_id, folder in removed.all():
            audit.record_fix_event(
                db,
                org_id=org_id,
                actor_user_id=identity.user.id,
                action=audit.FIX_CODE_LOCATION_CHANGED,
                target_kind=audit.CODE_LOCATION_TARGET,
                target_id=location_id,
                detail={
                    "service": row.service,
                    "project_id": None if row.project_id is None else str(row.project_id),
                    "removed": True,
                    "folder": folder,
                },
            )
    await db.commit()
