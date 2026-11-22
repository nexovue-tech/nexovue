"""F5's setup routes: the Nexovue suggestions App, source consent, code locations, the
readiness counts and a person's GitHub link (design, *Routes*; plan T3.3 to T3.6, T3.17).
HTTP only.

- The App and code locations are an admin's (`MayManageConnections`); consent is an
  owner's (`MayConsentAIDisclosure`, F3's action), so an admin and a member are refused
  (AC10a); the reads are every person's, a support view's without an owner's paths (AC11).
- The GitHub link is a person's own act on their own account (`CurrentIdentity`): a token
  is refused below it, and a support view by method, so it cannot create or change a link
  (AC11). Its writes are `POST`s, as F22's manifest return is.

**Nothing here calls GitHub** (AC5a): each step adds a job for `scheduler-connections`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from nexovue_platform.fixes import write_gate

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayConsentAIDisclosure, MayManageConnections
from nexovue_api.connections import fix_app, user_link
from nexovue_api.connections.fix_schemas import (
    CodeLocationRemovals,
    CodeLocations,
    CodeLocationsRequest,
    FixAppView,
    GitHubLinkAttemptView,
    GitHubLinkCompleteRequest,
    GitHubLinkStarted,
    GitHubLinkView,
    ReadinessCounts,
    RepositoryDraftSettings,
    RepositoryDraftSettingsRequest,
    RepositoryDraftSettingsView,
    SourceConsentOverview,
    SourceConsentRequest,
    SourceConsentView,
    SourceConsentWithdrawn,
    StartFixAppRequest,
)
from nexovue_api.connections.router import availability_of
from nexovue_api.connections.schemas import (
    CompleteGitHubRequest,
    GitHubSetupCompleted,
    GitHubSetupStarted,
)
from nexovue_api.deps import AppSettings, CurrentSession, DbSession
from nexovue_api.findings import fix_consent, fix_locations, fix_readiness, fix_settings

router = APIRouter(prefix="/connections/github", tags=["connections"])
code_locations_router = APIRouter(prefix="/code-locations", tags=["connections"])
readiness_router = APIRouter(prefix="/fix-readiness", tags=["connections"])

FolderQuery = Annotated[
    str | None, Query(description="A folder in the repository; may hold a slash", max_length=200)
]
ScopeQuery = Annotated[
    str | None, Query(description="`whole-repository`, instead of a folder", max_length=32)
]


# ----------------------------------------------------------------- the Fix App


@router.get("/fix-app", summary="The Nexovue suggestions App: its state, grant and repositories")
async def read_fix_app(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings
) -> FixAppView:
    return await fix_app.view(
        db,
        org_id=identity.org_id,
        support_view=identity.impersonation is not None,
        availability=availability_of(settings),
        now=datetime.now(UTC),
    )


@router.post(
    "/fix-app/manifest",
    status_code=status.HTTP_201_CREATED,
    summary="Start creating the Nexovue suggestions App, which reads code",
)
async def start_fix_app(
    identity: MayManageConnections,
    db: DbSession,
    settings: AppSettings,
    request: StartFixAppRequest,
) -> GitHubSetupStarted:
    return await fix_app.start_fix_app(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        availability=availability_of(settings),
        github_organization=request.github_organization,
        now=datetime.now(UTC),
    )


@router.post(
    "/fix-app/manifest/complete",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Take GitHub's redirect back, and have the App's key fetched",
)
async def complete_fix_app(
    identity: MayManageConnections,
    db: DbSession,
    settings: AppSettings,
    request: CompleteGitHubRequest,
) -> GitHubSetupCompleted:
    return await fix_app.complete_fix_app(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        state=request.state,
        code=request.code,
        keys=settings.connection_keys,
        availability=availability_of(settings),
        now=datetime.now(UTC),
    )


@router.post(
    "/fix-app/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Read again which repositories and permissions the App has",
)
async def refresh_fix_app(
    identity: MayManageConnections, db: DbSession, settings: AppSettings
) -> None:
    await fix_app.refresh_fix_app(
        db, org_id=identity.org_id, availability=availability_of(settings)
    )


# -------------------------------------------------------------- source consent


@router.get(
    "/repositories/source-consent",
    summary="Each repository's consent to send its code to the AI provider",
)
async def read_source_consent(identity: CurrentIdentity, db: DbSession) -> SourceConsentOverview:
    return await fix_consent.overview(db, identity=identity)


@router.put(
    "/repositories/{repository_id}/source-consents",
    summary="Agree to send one repository folder's code to the AI provider",
)
async def agree_source_consent(
    identity: MayConsentAIDisclosure,
    db: DbSession,
    repository_id: uuid.UUID,
    request: SourceConsentRequest,
    folder: FolderQuery = None,
    scope: ScopeQuery = None,
) -> SourceConsentView:
    return await fix_consent.agree(
        db,
        identity=identity,
        repository_id=repository_id,
        folder_scope=fix_consent.scope_of(folder, scope),
        request=request,
        now=datetime.now(UTC),
    )


@router.delete(
    "/repositories/{repository_id}/source-consents",
    summary="Withdraw consent: stop new reads, and delete the stored code it covered",
)
async def withdraw_source_consent(
    identity: MayConsentAIDisclosure,
    db: DbSession,
    repository_id: uuid.UUID,
    folder: FolderQuery = None,
    scope: ScopeQuery = None,
) -> SourceConsentWithdrawn:
    return await fix_consent.withdraw(
        db,
        identity=identity,
        repository_id=repository_id,
        folder_scope=fix_consent.scope_of(folder, scope),
        now=datetime.now(UTC),
    )


@router.get(
    "/repositories/fix-settings",
    summary="Each repository's draft pull request choices, set by an owner",
)
async def read_draft_settings(
    identity: CurrentIdentity, db: DbSession
) -> RepositoryDraftSettingsView:
    return await fix_settings.overview(
        db, identity=identity, go=write_gate.WRITE_ACCESS_GO, now=datetime.now(UTC)
    )


@router.put(
    "/repositories/{repository_id}/fix-settings",
    summary="Allow draft pull requests, or CI when a draft opens, for one repository",
)
async def change_draft_settings(
    identity: MayConsentAIDisclosure,
    db: DbSession,
    repository_id: uuid.UUID,
    request: RepositoryDraftSettingsRequest,
) -> RepositoryDraftSettings:
    """BR14, D45: an owner's act, audited; Nexovue's limit, not GitHub's."""
    return await fix_settings.change(
        db,
        identity=identity,
        repository_id=repository_id,
        request=request,
        now=datetime.now(UTC),
    )


# --------------------------------------------------------- the GitHub account link


@router.post(
    "/user-link/start",
    status_code=status.HTTP_201_CREATED,
    summary="Start linking your own GitHub account",
)
async def start_user_link(
    identity: CurrentIdentity, session: CurrentSession, db: DbSession, settings: AppSettings
) -> GitHubLinkStarted:
    return await user_link.start(
        db,
        org_id=identity.org_id,
        user_id=identity.user.id,
        session=session,
        app_base_url=settings.app_base_url,
        now=datetime.now(UTC),
    )


@router.post(
    "/user-link/complete",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Take GitHub's redirect back for your own account link",
)
async def complete_user_link(
    identity: CurrentIdentity,
    session: CurrentSession,
    db: DbSession,
    settings: AppSettings,
    request: GitHubLinkCompleteRequest,
) -> GitHubLinkAttemptView:
    return await user_link.complete(
        db,
        org_id=identity.org_id,
        user_id=identity.user.id,
        session=session,
        state=request.state,
        code=request.code,
        keys=settings.connection_keys,
        now=datetime.now(UTC),
    )


@router.get("/user-link/attempts/{attempt_id}", summary="How your account link attempt went")
async def read_user_link_attempt(
    identity: CurrentIdentity, db: DbSession, attempt_id: uuid.UUID
) -> GitHubLinkAttemptView:
    return await user_link.attempt_of(
        db, org_id=identity.org_id, user_id=identity.user.id, attempt_id=attempt_id
    )


@router.get("/user-link", summary="Your own linked GitHub account, if any")
async def read_user_link(identity: CurrentIdentity, db: DbSession) -> GitHubLinkView:
    return await user_link.link_of(db, org_id=identity.org_id, user_id=identity.user.id)


@router.delete(
    "/user-link", status_code=status.HTTP_204_NO_CONTENT, summary="Remove your own GitHub link"
)
async def remove_user_link(identity: CurrentIdentity, db: DbSession) -> None:
    await user_link.unlink(db, org_id=identity.org_id, user_id=identity.user.id)


# ----------------------------------------------------------------- code locations


@code_locations_router.get("", summary="Where each service's code lives, for suggested fixes")
async def read_code_locations(identity: CurrentIdentity, db: DbSession) -> CodeLocations:
    return await fix_locations.list_locations(db, identity=identity)


@code_locations_router.put("", summary="Set where services' code lives, many at once")
async def set_code_locations(
    identity: MayManageConnections, db: DbSession, request: CodeLocationsRequest
) -> CodeLocations:
    return await fix_locations.set_locations(
        db, identity=identity, request=request, now=datetime.now(UTC)
    )


@code_locations_router.delete(
    "", status_code=status.HTTP_204_NO_CONTENT, summary="Remove services' code locations"
)
async def remove_code_locations(
    identity: MayManageConnections, db: DbSession, request: CodeLocationRemovals
) -> None:
    await fix_locations.remove_locations(db, identity=identity, request=request)


# ---------------------------------------------------------------------- readiness


@readiness_router.get("", summary="How a project's recent findings would reach a suggested fix")
async def read_fix_readiness(
    identity: CurrentIdentity,
    db: DbSession,
    settings: AppSettings,
    project_id: Annotated[uuid.UUID, Query()],
) -> ReadinessCounts:
    return await fix_readiness.readiness(
        db,
        org_id=identity.org_id,
        project_id=project_id,
        show_held=settings.show_held_causes,
        now=datetime.now(UTC),
    )
