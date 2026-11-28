"""An owner's per-repository choices for drafts (spec BR14, D45, AC60, AC61; design,
`/settings/ai`, per repository; plan T7.7).

- `GET /connections/github/repositories/fix-settings`: every reachable repository of the
  Fix App, with "Allow draft pull requests" and "Run CI when a draft opens", each off until
  an owner sets it, and whether GitHub refused a draft there in the last 24 hours. Every
  person reads it; only an owner sees the controls (the screen).
- `PUT .../{repository_id}/fix-settings`: an owner's act (`MayConsentAIDisclosure`), audited
  as `fix.repository_settings_changed` with the old and new values.

**These are Nexovue's limits, not GitHub's** (BR14): the App's write grant reaches every
repository it is installed on. Allowing drafts here writes nothing anywhere: write access
stays off in code until the founder's go (`write_gate.py`, ADR-097), which the read says.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from nexovue_platform.fixes.records import (
    DRAFT_REFUSAL_HOLDS_FOR,
    FIX_REPOSITORY_SETTINGS_CHANGED,
    WRITES_OFF,
)
from nexovue_platform.fixes.sentences import DRAFT_REFUSAL_SENTENCES
from nexovue_platform.fixes.write_gate import WriteGo, write_access_on
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.connections.fix_schemas import (
    RepositoryDraftSettings,
    RepositoryDraftSettingsRequest,
    RepositoryDraftSettingsView,
)
from nexovue_api.data.postgres.models import ConnectionRepository, FixRepositorySettings
from nexovue_api.errors.exceptions import NotFoundError
from nexovue_api.findings import fix_access

NO_SUCH_REPOSITORY = "No such repository on the Nexovue suggestions App."


async def overview(
    db: AsyncSession, *, identity: Identity, go: WriteGo | None, now: datetime
) -> RepositoryDraftSettingsView:
    org_id = identity.org_id
    app = await fix_access.fix_app(db, org_id=org_id)
    rows = (
        []
        if app is None
        else (
            await db.execute(
                select(ConnectionRepository, FixRepositorySettings)
                .outerjoin(
                    FixRepositorySettings,
                    (FixRepositorySettings.org_id == ConnectionRepository.org_id)
                    & (FixRepositorySettings.connection_repository_id == ConnectionRepository.id),
                )
                .where(
                    ConnectionRepository.org_id == org_id,
                    ConnectionRepository.connection_id == app.id,
                )
                .order_by(ConnectionRepository.key)
            )
        ).all()
    )
    return RepositoryDraftSettingsView(
        available=app is not None,
        writes_off=None if write_access_on(go) else DRAFT_REFUSAL_SENTENCES[WRITES_OFF],
        repositories=[_one(place, settings, now=now) for place, settings in rows],
    )


def _one(
    place: ConnectionRepository, settings: FixRepositorySettings | None, *, now: datetime
) -> RepositoryDraftSettings:
    refused_at = None if settings is None else settings.draft_refused_at
    return RepositoryDraftSettings(
        repository_id=place.id,
        repository=place.key,
        is_private=place.is_private,
        drafts_allowed=settings is not None and settings.drafts_allowed,
        ci_on_open=settings is not None and settings.ci_on_open,
        draft_refused_recently=refused_at is not None
        and now - refused_at < DRAFT_REFUSAL_HOLDS_FOR,
        set_at=None if settings is None else settings.set_at,
    )


async def change(
    db: AsyncSession,
    *,
    identity: Identity,
    repository_id: uuid.UUID,
    request: RepositoryDraftSettingsRequest,
    now: datetime,
) -> RepositoryDraftSettings:
    """An owner's choice for one repository, audited with what it was before (AC60, AC61)."""
    org_id = identity.org_id
    reached = await fix_access.fix_app_repository(db, org_id=org_id, repository_id=repository_id)
    if reached is None:
        raise NotFoundError(NO_SUCH_REPOSITORY)
    place = reached[1]
    settings: FixRepositorySettings | None = await db.scalar(
        select(FixRepositorySettings)
        .where(
            FixRepositorySettings.org_id == org_id,
            FixRepositorySettings.connection_repository_id == repository_id,
        )
        .with_for_update()
    )
    before = {
        "drafts_allowed": settings is not None and settings.drafts_allowed,
        "ci_on_open": settings is not None and settings.ci_on_open,
    }
    if settings is None:
        settings = FixRepositorySettings(
            org_id=org_id, connection_repository_id=repository_id, set_at=now
        )
        db.add(settings)
    settings.drafts_allowed = request.drafts_allowed
    settings.ci_on_open = request.ci_on_open
    settings.set_by = identity.user.id
    settings.set_at = now
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=identity.user.id,
        action=FIX_REPOSITORY_SETTINGS_CHANGED,
        target_kind=audit.REPOSITORY_TARGET,
        target_id=repository_id,
        detail={
            "before": before,
            "after": {"drafts_allowed": request.drafts_allowed, "ci_on_open": request.ci_on_open},
        },
    )
    await db.commit()
    return _one(place, settings, now=now)
