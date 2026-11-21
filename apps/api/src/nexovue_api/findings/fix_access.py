"""Whether Nexovue may read a service's code: its code location, the Fix App's reach and
grant, and a live source consent (spec FR5b, FR6, FR8, FR9 rows 8 to 10; plan T3.7).

Postgres only, **no GitHub call and no AI module**: the page polls this, and the AI
connection is read here as stored terms, compared through `nexovue_platform.fixes.consent`.
Every statement binds the organization; the path from a person to a repository is
`org_id` → `service_code_locations` → `connection_repositories` → `connections` (design,
*Tenancy path*), each compared in the statement and held by composite keys.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from nexovue_platform.connections import DISCONNECTED
from nexovue_platform.fixes.consent import ProviderTerms, folder_covered, terms_match
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AIConnection,
    Connection,
    ConnectionRepository,
    FixSourceConsent,
    ServiceCodeLocation,
)

FIX_APP_KIND: Final = "github_fix"
# The grant rungs 3 and 4 need (design, *Fix App*): `read` or more on contents.
READABLE: Final = frozenset({"read", "write"})


@dataclass(frozen=True, slots=True)
class CodeAccess:
    """What FR9's rows 8 to 10 read, and the consent and folder a click copies."""

    location: ServiceCodeLocation | None
    repository: ConnectionRepository | None
    app: Connection | None
    consent: FixSourceConsent | None

    @property
    def read_permission(self) -> bool:
        app, repository = self.app, self.repository
        if app is None or repository is None or app.github_installation_id is None:
            return False
        granted = (app.granted_permissions or {}).get("contents")
        return granted in READABLE and repository.is_reachable

    @property
    def folder(self) -> str | None:
        return None if self.location is None else self.location.folder


def terms_of(connection: AIConnection | None) -> ProviderTerms | None:
    """The AI connection's terms now, or None when AI is off."""
    if connection is None or not connection.enabled:
        return None
    return ProviderTerms(
        base_url=connection.base_url,
        wire_format=connection.wire_format,
        key_id=None if connection.key_id is None else str(connection.key_id),
        model=connection.model,
    )


def agreed_terms(consent: FixSourceConsent) -> ProviderTerms:
    return ProviderTerms(
        base_url=consent.provider_base_url,
        wire_format=consent.provider_format,
        key_id=None if consent.key_id is None else str(consent.key_id),
        model=consent.model,
    )


def host_of(base_url: str) -> str:
    """A provider's host alone, as the consent sentences name it."""
    return urlsplit(base_url).hostname or base_url


def covering_consent(
    consents: Sequence[FixSourceConsent], folder: str | None, current: ProviderTerms | None
) -> FixSourceConsent | None:
    """FR8: a live consent whose scope covers `folder` and whose terms equal the AI
    connection's now. A whole-repository consent is tried last."""
    ordered = sorted(consents, key=lambda consent: consent.folder_scope is None)
    for consent in ordered:
        if (
            consent.withdrawn_at is None
            and folder_covered(consent.folder_scope, folder)
            and terms_match(agreed_terms(consent), current)
        ):
            return consent
    return None


async def ai_connection(db: AsyncSession, *, org_id: uuid.UUID) -> AIConnection | None:
    found: AIConnection | None = await db.scalar(
        select(AIConnection).where(AIConnection.org_id == org_id)
    )
    return found


async def location_of(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service_name: str
) -> ServiceCodeLocation | None:
    """A project's own row wins over the organization's (FR5b, AC12a)."""
    rows = await db.scalars(
        select(ServiceCodeLocation)
        .where(
            ServiceCodeLocation.org_id == org_id,
            ServiceCodeLocation.service_name == service_name,
            or_(
                ServiceCodeLocation.project_id == project_id,
                ServiceCodeLocation.project_id.is_(None),
            ),
        )
        .order_by(ServiceCodeLocation.project_id.is_(None))
    )
    return rows.first()


async def fix_app(db: AsyncSession, *, org_id: uuid.UUID) -> Connection | None:
    found: Connection | None = await db.scalar(
        select(Connection).where(
            Connection.org_id == org_id,
            Connection.kind == FIX_APP_KIND,
            Connection.state != DISCONNECTED,
        )
    )
    return found


async def fix_app_repository(
    db: AsyncSession, *, org_id: uuid.UUID, repository_id: uuid.UUID
) -> tuple[Connection, ConnectionRepository] | None:
    """A repository only if the organization's live Fix App reaches it."""
    found = (
        await db.execute(
            select(Connection, ConnectionRepository)
            .join(
                ConnectionRepository,
                (ConnectionRepository.org_id == Connection.org_id)
                & (ConnectionRepository.connection_id == Connection.id),
            )
            .where(
                Connection.org_id == org_id,
                Connection.kind == FIX_APP_KIND,
                Connection.state != DISCONNECTED,
                ConnectionRepository.id == repository_id,
            )
        )
    ).first()
    return None if found is None else (found[0], found[1])


async def live_consents(
    db: AsyncSession, *, org_id: uuid.UUID, repository_id: uuid.UUID
) -> list[FixSourceConsent]:
    rows = await db.scalars(
        select(FixSourceConsent).where(
            FixSourceConsent.org_id == org_id,
            FixSourceConsent.connection_repository_id == repository_id,
            FixSourceConsent.withdrawn_at.is_(None),
        )
    )
    return list(rows)


async def code_access(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    service_name: str | None,
    current: ProviderTerms | None,
) -> CodeAccess:
    nothing = CodeAccess(location=None, repository=None, app=None, consent=None)
    if service_name is None:
        return nothing
    location = await location_of(
        db, org_id=org_id, project_id=project_id, service_name=service_name
    )
    if location is None:
        return nothing
    reached = await fix_app_repository(
        db, org_id=org_id, repository_id=location.connection_repository_id
    )
    if reached is None:
        return CodeAccess(location=location, repository=None, app=None, consent=None)
    app, repository = reached
    consents = await live_consents(db, org_id=org_id, repository_id=repository.id)
    return CodeAccess(
        location=location,
        repository=repository,
        app=app,
        consent=covering_consent(consents, location.folder, current),
    )
