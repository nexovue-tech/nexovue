"""Tracker connections (F22, FR28 to FR34). Reading what is connected is every person's;
everything that changes a connection is an owner's or admin's (`MANAGE_CONNECTIONS`).

GitHub's redirect lands on a console route, which posts its `code` and `state` here:
writing on a `POST`, not a `GET` callback, keeps the step under the support view's
read-only rule and the authorization coverage test (design, step 3).

`POST /connections/webhook/{connection_id}` is the one route here with no session: a
tracker's webhook, proved by its HMAC over the raw body, so the body is read as bytes, and
bounded before it is buffered, as the deploy webhook's is. It only rings the doorbell
(ADR-035).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Final

from fastapi import APIRouter, Query, Request, status

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.policy import MayManageConnections
from nexovue_api.connections import places, service
from nexovue_api.connections.schemas import (
    Availability,
    CompleteGitHubRequest,
    ConnectionPlace,
    ConnectionsOverview,
    ConnectionTestAccepted,
    GitHubSetupCompleted,
    GitHubSetupStarted,
    MapServiceRequest,
    PlaceChange,
    ReplaceCredentialRequest,
    ServiceRepositories,
    ServiceRepositoryView,
    StartGitHubRequest,
)
from nexovue_api.data.postgres.models import SERVICE_NAME_LENGTH
from nexovue_api.deps import AppSettings, DbSession, EmailAvailable
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.settings import Settings

router = APIRouter(prefix="/connections", tags=["connections"])
# Which repository each service's issues go to (design, *API*): read by every person,
# for the create panel's preselection; changed by an owner or admin.
service_repositories_router = APIRouter(prefix="/service-repositories", tags=["connections"])

# D29, a guess: an `issues` delivery is a few kilobytes, and a refused one costs nothing,
# since polling is the base. The edge allows far more (`Caddyfile`, 65 MiB).
MAX_WEBHOOK_BYTES: Final = 1024 * 1024
WEBHOOK_TOO_LARGE: Final = "A webhook delivery must be at most 1 MiB."


def availability_of(settings: Settings) -> Availability:
    return Availability(
        deployment=settings.nexovue_deployment,
        enabled=settings.connections_enabled,
        app_base_url=settings.app_base_url,
    )


@router.post(
    "/webhook/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="A tracker's signed webhook: read the issue it names sooner, and nothing else",
)
async def receive_connection_webhook(
    db: DbSession, settings: AppSettings, request: Request, connection_id: uuid.UUID
) -> None:
    await service.ring_from_webhook(
        db,
        connection_id=connection_id,
        body=await _bounded_body(request),
        headers=request.headers,
        keys=settings.connection_keys,
        now=datetime.now(UTC),
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


@router.patch(
    "/{connection_id}/places/{place_id}",
    summary="Allow issues in a public repository, or withdraw that",
)
async def change_place(
    identity: MayManageConnections,
    db: DbSession,
    settings: AppSettings,
    connection_id: uuid.UUID,
    place_id: uuid.UUID,
    request: PlaceChange,
) -> ConnectionPlace:
    """FR42, AC115: each audited. The console warns first what becomes public."""
    return await places.set_public_allowed(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        connection_id=connection_id,
        place_id=place_id,
        allowed=request.public_allowed,
        availability=availability_of(settings),
        now=datetime.now(UTC),
    )


@service_repositories_router.get("", summary="Which repository each service's issues go to")
async def list_service_repositories(
    identity: CurrentIdentity,
    db: DbSession,
    project_id: Annotated[uuid.UUID | None, Query(description="Only this project's")] = None,
) -> ServiceRepositories:
    """A support view reads no repository names, as `GET /connections` gives it none."""
    if identity.impersonation is not None:
        return ServiceRepositories(mappings=[])
    return ServiceRepositories(
        mappings=await places.mappings(db, org_id=identity.org_id, project_id=project_id)
    )


@service_repositories_router.put("", summary="Set the repository a service's issues go to")
async def map_service_repository(
    identity: MayManageConnections, db: DbSession, request: MapServiceRequest
) -> ServiceRepositoryView:
    return await places.map_service(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        project_id=request.project_id,
        service_name=request.service_name,
        place_id=request.place_id,
        now=datetime.now(UTC),
    )


@service_repositories_router.delete(
    "", status_code=status.HTTP_204_NO_CONTENT, summary="Stop preselecting a service's repository"
)
async def unmap_service_repository(
    identity: MayManageConnections,
    db: DbSession,
    project_id: Annotated[uuid.UUID, Query()],
    service_name: Annotated[str, Query(min_length=1, max_length=SERVICE_NAME_LENGTH)],
) -> None:
    await places.unmap_service(
        db,
        org_id=identity.org_id,
        actor_user_id=identity.user.id,
        project_id=project_id,
        service_name=service_name,
    )


async def _bounded_body(request: Request) -> bytes:
    """The raw bytes, refused at the first byte past the cap: Content-Length is the
    caller's word, so the cap holds on what arrives."""
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_WEBHOOK_BYTES:
            raise InvalidRequestError(WEBHOOK_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)
