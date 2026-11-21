"""An owner's agreement to send a repository folder's code to the AI provider (spec FR6 to
FR8, AC9, AC10a, AC12c; design, *`/settings/ai`: Source consent*; plan T3.5; ADR-084).

- **Owners only** (`MayConsentAIDisclosure`, F3's action), per repository and folder; a
  monorepo may agree to `services/a` and not `services/b` (AC12c). The route takes exactly
  one of `folder=<path>` or `scope=whole-repository`.
- **The owner must answer** whether quoted code needs a linked GitHub account with read
  access (D20); nothing is preselected, so the request has no default for it.
- A consent records the provider's full address, wire format, key id and model as they are
  now. **Whether it is live is worked out at each use** (FR8): a change to any of them stops
  the next request with `no_consent`, and no statement touches a consent row to end it
  (AC9). Agreeing again to the same folder updates the row in place.
- **Withdrawing** ends, in the same transaction, every suggestion of that repository still
  being prepared (`failed`, `consent_ended_in_flight`, its pack gone), and deletes the
  stored code of every suggestion it covered, through `fix_takeback` and the one
  statement (`clear_stored_code`). The answer says how many.

Every statement binds the caller's organization. A support view reads states, never an
owner's extra paths (AC11).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.fixes.consent import ProviderTerms, folder_covered, normal_folder
from nexovue_platform.fixes.policy import NEVER_SENT_HARD
from nexovue_platform.fixes.records import (
    CODE_CONSENT_WITHDRAWN,
    CONSENT_ENDED_IN_FLIGHT,
    CONSENT_WITHDRAWN_BY_OWNER,
    KIND_SOURCE,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.permissions import ALLOWED, Action
from nexovue_api.connections.fix_schemas import (
    RepositoryConsents,
    SourceConsentOverview,
    SourceConsentRequest,
    SourceConsentView,
    SourceConsentWithdrawn,
)
from nexovue_api.data.postgres.models import (
    ConnectionRepository,
    FindingFix,
    FixSourceConsent,
    User,
)
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.findings import fix_access, fix_takeback

WHOLE_REPOSITORY_SCOPE: Final = "whole-repository"
# FR7's sentences, exact (spec FR7). The first is the provider sentence; the next the
# gateway, secret-scan and outbound sentences; the last how long code lasts (D29).
CONSENT_SENTENCES: Final = (
    "Nexovue cannot see or control what this provider keeps or trains on; check your "
    "contract. The secret scan is a net: a secret in an unrecognized format in a chosen "
    "file will be sent.",
    "On a gateway, the model name can decide which company receives this code, so changing "
    "the model asks you again.",
    "The AI process can reach the network unless your operator limits it; here is how.",
    "Quoted code is kept at most 30 days, and less when a suggestion ends; backups hold it "
    "up to about 4 weeks.",
)
_SCOPE_EXACTLY_ONE: Final = (
    "Name a folder, or the whole repository, but not both: `folder=<path>` or "
    "`scope=whole-repository`."
)
_BAD_FOLDER: Final = (
    "A folder is a relative path with no `..`, no leading `/` and no control character."
)
_BAD_PATH: Final = "Each path is a relative path inside the repository, with no control character."
_NO_AI: Final = "Set up AI first: a consent names the provider it sends code to."
_NO_REPOSITORY: Final = "The Nexovue suggestions App does not reach this repository."
_NO_CONSENT: Final = "No consent for this folder."
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")


def scope_of(folder: str | None, scope: str | None) -> str | None:
    """The one query parameter, as a stored `folder_scope` (None for the whole repository)."""
    if (folder is None) == (scope is None):
        raise InvalidRequestError(_SCOPE_EXACTLY_ONE, reason="scope")
    if scope is not None:
        if scope != WHOLE_REPOSITORY_SCOPE:
            raise InvalidRequestError(_SCOPE_EXACTLY_ONE, reason="scope")
        return None
    try:
        normal = normal_folder(folder)
    except ValueError as refused:
        raise InvalidRequestError(_BAD_FOLDER, reason="folder") from refused
    if normal is None:
        raise InvalidRequestError(_BAD_FOLDER, reason="folder")
    return normal


def _owner_paths(paths: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in paths:
        path = raw.strip().strip("/")
        if not path or len(path) > 200 or _CONTROL.search(path) or ".." in path.split("/"):
            raise InvalidRequestError(_BAD_PATH, reason="paths")
        if path not in cleaned:
            cleaned.append(path)
    return cleaned


async def overview(db: AsyncSession, *, identity: Identity) -> SourceConsentOverview:
    """`GET /connections/github/repositories/source-consent`: every reachable repository
    of the Fix App, with its consents and their standing now."""
    org_id = identity.org_id
    support_view = identity.impersonation is not None
    app = await fix_access.fix_app(db, org_id=org_id)
    connection = await fix_access.ai_connection(db, org_id=org_id)
    current = fix_access.terms_of(connection)
    repositories = (
        []
        if app is None
        else list(
            await db.scalars(
                select(ConnectionRepository)
                .where(
                    ConnectionRepository.org_id == org_id,
                    ConnectionRepository.connection_id == app.id,
                )
                .order_by(ConnectionRepository.key)
            )
        )
    )
    consents = list(
        await db.scalars(
            select(FixSourceConsent).where(
                FixSourceConsent.org_id == org_id, FixSourceConsent.withdrawn_at.is_(None)
            )
        )
    )
    return SourceConsentOverview(
        available=app is not None,
        provider_host=None if connection is None else fix_access.host_of(connection.base_url),
        model=None if connection is None else connection.model,
        region=None if connection is None else connection.region_label,
        requesters=await _requesters(db, org_id=org_id),
        repositories=[
            RepositoryConsents(
                repository_id=repository.id,
                repository=repository.key,
                is_private=repository.is_private,
                is_reachable=repository.is_reachable,
                consents=[
                    _view(consent, current, support_view=support_view)
                    for consent in consents
                    if consent.connection_repository_id == repository.id
                ],
            )
            for repository in repositories
        ],
        sentences=list(CONSENT_SENTENCES),
        never_sent=list(NEVER_SENT_HARD),
    )


def _view(
    consent: FixSourceConsent, current: ProviderTerms | None, *, support_view: bool
) -> SourceConsentView:
    live = fix_access.covering_consent([consent], consent.folder_scope, current)
    return SourceConsentView(
        id=consent.id,
        folder_scope=consent.folder_scope,
        provider_host=fix_access.host_of(consent.provider_base_url),
        model=consent.model,
        require_github_access=consent.require_github_access,
        consented_at=consent.consented_at,
        standing="live" if live is not None else "ended",
        extra_excluded_paths=None if support_view else list(consent.extra_excluded_paths),
        allowed_config_paths=None if support_view else list(consent.allowed_config_paths),
    )


async def _requesters(db: AsyncSession, *, org_id: uuid.UUID) -> int:
    """How many members hold `REQUEST_FIX` today (FR6's question)."""
    count = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.org_id == org_id,
            User.removed_at.is_(None),
            User.role.in_(sorted(ALLOWED[Action.REQUEST_FIX])),
        )
    )
    return int(count or 0)


async def agree(
    db: AsyncSession,
    *,
    identity: Identity,
    repository_id: uuid.UUID,
    folder_scope: str | None,
    request: SourceConsentRequest,
    now: datetime,
) -> SourceConsentView:
    """An owner agrees for one repository and folder, to the provider as it is now."""
    org_id = identity.org_id
    reached = await fix_access.fix_app_repository(db, org_id=org_id, repository_id=repository_id)
    if reached is None:
        raise NotFoundError(_NO_REPOSITORY)
    connection = await fix_access.ai_connection(db, org_id=org_id)
    if connection is None:
        raise ConflictError(_NO_AI, code="ai_off")
    excluded = _owner_paths(request.extra_excluded_paths)
    allowed = _owner_paths(request.allowed_config_paths)
    existing = await db.scalar(
        select(FixSourceConsent)
        .where(
            FixSourceConsent.org_id == org_id,
            FixSourceConsent.connection_repository_id == repository_id,
            FixSourceConsent.withdrawn_at.is_(None),
            FixSourceConsent.folder_scope.is_(None)
            if folder_scope is None
            else FixSourceConsent.folder_scope == folder_scope,
        )
        .with_for_update()
    )
    consent = existing or FixSourceConsent(
        id=uuid.uuid4(),
        org_id=org_id,
        connection_repository_id=repository_id,
        folder_scope=folder_scope,
    )
    consent.provider_base_url = connection.base_url
    consent.provider_format = connection.wire_format
    consent.key_id = connection.key_id
    consent.model = connection.model
    consent.require_github_access = request.require_github_access
    consent.extra_excluded_paths = excluded
    consent.allowed_config_paths = allowed
    consent.consented_by = identity.user.id
    consent.consented_at = now
    consent.ended_notice_sent_at = None
    if existing is None:
        db.add(consent)
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=identity.user.id,
        action=audit.AI_SOURCE_DISCLOSURE_AGREED,
        target_kind=audit.REPOSITORY_TARGET,
        target_id=repository_id,
        detail={
            "consent_id": str(consent.id),
            "folder_scope": folder_scope,
            "provider_host": fix_access.host_of(connection.base_url),
            "model": connection.model,
            "require_github_access": request.require_github_access,
            "allowed_config_paths": allowed,
        },
    )
    await db.flush()
    await db.commit()
    return _view(consent, fix_access.terms_of(connection), support_view=False)


async def withdraw(
    db: AsyncSession,
    *,
    identity: Identity,
    repository_id: uuid.UUID,
    folder_scope: str | None,
    now: datetime,
) -> SourceConsentWithdrawn:
    """Withdraw, end the work in flight it covered, and delete its stored code (design,
    *Failure handling*; BR9's "an owner withdraws" row)."""
    org_id = identity.org_id
    consent = await db.scalar(
        select(FixSourceConsent)
        .where(
            FixSourceConsent.org_id == org_id,
            FixSourceConsent.connection_repository_id == repository_id,
            FixSourceConsent.withdrawn_at.is_(None),
            FixSourceConsent.folder_scope.is_(None)
            if folder_scope is None
            else FixSourceConsent.folder_scope == folder_scope,
        )
        .with_for_update()
    )
    if consent is None:
        raise NotFoundError(_NO_CONSENT)
    consent.withdrawn_at, consent.withdrawn_by = now, identity.user.id
    consent.withdrawn_reason = CONSENT_WITHDRAWN_BY_OWNER
    others = [
        each
        for each in await fix_access.live_consents(db, org_id=org_id, repository_id=repository_id)
        if each.id != consent.id
    ]
    rows = list(
        await db.scalars(
            select(FindingFix)
            .where(
                FindingFix.org_id == org_id,
                FindingFix.kind == KIND_SOURCE,
                FindingFix.connection_repository_id == repository_id,
                FindingFix.code_deleted_at.is_(None),
            )
            .with_for_update()
        )
    )
    covered = [
        row
        for row in rows
        if folder_covered(folder_scope, row.folder)
        and not any(folder_covered(each.folder_scope, row.folder) for each in others)
    ]
    deleted = await fix_takeback.end_and_clear(
        db,
        covered,
        in_flight_reason=CONSENT_ENDED_IN_FLIGHT,
        code_reason=CODE_CONSENT_WITHDRAWN,
        now=now,
    )
    audit.record_fix_event(
        db,
        org_id=org_id,
        actor_user_id=identity.user.id,
        action=audit.AI_SOURCE_DISCLOSURE_WITHDRAWN,
        target_kind=audit.REPOSITORY_TARGET,
        target_id=repository_id,
        detail={
            "consent_id": str(consent.id),
            "folder_scope": folder_scope,
            "code_deleted": deleted,
        },
    )
    await db.commit()
    return SourceConsentWithdrawn(code_deleted=deleted)
