"""Tracker connections (F22, FR28 to FR34). Reading what is connected is every person's;
everything that changes a connection is an owner's or admin's (`MANAGE_CONNECTIONS`).

GitHub's redirect lands on a console route, which posts its `code` and `state` here:
writing on a `POST`, not a `GET` callback, keeps the step under the support view's
read-only rule and the authorization coverage test (design, step 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayManageConnections
from nexovue_api.connections import service
from nexovue_api.connections.schemas import (
    Availability,
    CompleteGitHubRequest,
    ConnectionsOverview,
    ConnectionTestAccepted,
    GitHubSetupCompleted,
    GitHubSetupStarted,
    ReplaceCredentialRequest,
    StartGitHubRequest,
)
from nexovue_api.deps import AppSettings, DbSession, EmailAvailable
from nexovue_api.settings import Settings

router = APIRouter(prefix="/connections", tags=["connections"])


def availability_of(settings: Settings) -> Availability:
    return Availability(
        deployment=settings.nexovue_deployment,
        enabled=settings.connections_enabled,
        app_base_url=settings.app_base_url,
    )


@router.get("", summary="What this organization has connected, and whether it works")
async def list_connections(
    identity: CurrentIdentity, db: DbSession, settings: AppSettings, email: EmailAvailable
) -> ConnectionsOverview:
    return await service.overview(
        db,
        org_id=identity.org_id,
        support_view=identity.impersonation is not None,
        availability=availability_of(settings),
        email_available=email,
    )


@router.post(
    "/github/manifest",
    status_code=status.HTTP_201_CREATED,
    summary="Start creating this organization's private GitHub App",
)
async def start_github(
    identity: MayManageConnections,
    db: DbSession,
    settings: AppSettings,
    request: StartGitHubRequest,
) -> GitHubSetupStarted:
    return await service.start_github(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        availability=availability_of(settings),
        reachable=request.reachable,
        github_organization=request.github_organization,
        now=datetime.now(UTC),
    )


@router.post(
    "/github/manifest/complete",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Take GitHub's redirect back, and have the App's key fetched",
)
async def complete_github(
    identity: MayManageConnections,
    db: DbSession,
    settings: AppSettings,
    request: CompleteGitHubRequest,
) -> GitHubSetupCompleted:
    return await service.complete_github(
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
    "/github/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Read again which repositories the App reaches",
)
async def refresh_github(
    identity: MayManageConnections, db: DbSession, settings: AppSettings
) -> None:
    await service.refresh_github(db, org_id=identity.org_id, availability=availability_of(settings))


@router.post(
    "/{connection_id}/credential",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replace the App's private key in place, and test it",
)
async def replace_credential(
    identity: MayManageConnections,
    db: DbSession,
    settings: AppSettings,
    connection_id: uuid.UUID,
    request: ReplaceCredentialRequest,
) -> ConnectionTestAccepted:
    return await service.replace_credential(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        connection_id=connection_id,
        private_key=request.private_key,
        keys=settings.connection_keys,
        availability=availability_of(settings),
    )


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect: delete the stored key and stop all sync",
)
async def disconnect(
    identity: MayManageConnections, db: DbSession, connection_id: uuid.UUID
) -> None:
    await service.disconnect(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        connection_id=connection_id,
        hooks=tuple(service.DISCONNECT_HOOKS),
    )
