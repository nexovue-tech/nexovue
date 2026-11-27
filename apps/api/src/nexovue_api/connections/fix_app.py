"""F5's own GitHub App, "Nexovue suggestions" (design, *Fix App*; plan T3.3; ADR-082).

Its own App, apart from F22's issue App, because permissions belong to an App: raising
F22's to read code would reach every repository connected for issues (D36). Set up
through F22's manifest flow and its connection row (`kind = 'github_fix'`):

- **Read only, here:** `contents: read` and `metadata: read`. Write is added the first
  time a draft is wanted (Step 7), by editing the App on GitHub, behind the founder's go.
- **No webhook** (no `hook_attributes`: GitHub refuses an unreachable hook even inactive):
  F5 polls (D33). The account link's callback lands on a console route, which posts here,
  so no public address is needed.
- The conversion keeps the App's key and its OAuth client id and secret, both sealed
  under `CONNECTION_SECRET_KEY` by the job (`scheduler-connections`).

**Nothing here calls GitHub** (AC5a): each step adds a job. Every statement binds the
caller's organization.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Final

from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.connections import (
    CONNECTION_MANIFEST_CODE_PURPOSE,
    CONNECTIONS_INTERACTIVE_QUEUE,
    DISCONNECTED,
    SETTING_UP,
    connection_job_arguments,
    connection_secret_owner,
    is_github_account,
    is_manifest_code,
)
from nexovue_platform.fixes.records import CONVERT_FIX_APP_TASK, REFRESH_FIX_APP_TASK
from nexovue_platform.jobs import JobRequest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.connections import jobs, queries
from nexovue_api.connections.fix_schemas import FixAppRepository, FixAppView
from nexovue_api.connections.schemas import Availability, GitHubSetupCompleted, GitHubSetupStarted
from nexovue_api.connections.service import (
    APP_NAME_LIMIT,
    CONVERSION_ATTEMPTS,
    SETUP_LIFETIME,
    TEST_EXPIRY_SECONDS,
    hash_of_state,
    sealed_box,
    setup_step,
)
from nexovue_api.data.postgres.models import Connection
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError

FIX_APP_KIND: Final = "github_fix"
FIX_APP_NAME_PREFIX: Final = "nexovue-suggestions-"
# Rungs 3 and 4 need only these (design, *Fix App*, tiers).
FIX_APP_PERMISSIONS: Final = {"contents": "read", "metadata": "read"}
# The console routes GitHub returns a person to (`app/router.tsx`).
FIX_APP_RETURN_PATH: Final = "/settings/connections/fix-app/return"
LINK_CALLBACK_PATH: Final = "/settings/github-link/return"

_ALREADY: Final = (
    "The Nexovue suggestions App is already set up, or a setup is in progress. Finish it, "
    "or disconnect it first."
)
_INVALID_SETUP: Final = "This setup link is not valid. Nothing was changed."
_EXPIRED_SETUP: Final = "This setup link has expired. Nothing was changed."
_NOT_SET_UP: Final = "The Nexovue suggestions App is not set up yet."
_ORGANIZATION_NAME: Final = "That is not a GitHub organization name."


def _refuse_where_unavailable(availability: Availability) -> None:
    if availability.refusal is not None:
        raise ConflictError(availability.refusal)


def fix_app_manifest(*, base_url: str, organization_slug: str) -> dict[str, object]:
    """The App GitHub is asked to create. No `hook_attributes`: GitHub refuses a hook URL it
    cannot reach even when inactive (live test, 2026-11-27), and F5 polls. Still unverified
    until a real run: whether GitHub keeps `callback_urls` on a manifest App."""
    return {
        "name": f"{FIX_APP_NAME_PREFIX}{organization_slug}"[:APP_NAME_LIMIT],
        "url": base_url,
        "redirect_url": base_url + FIX_APP_RETURN_PATH,
        "callback_urls": [base_url + LINK_CALLBACK_PATH],
        "request_oauth_on_install": False,
        "public": False,
        "default_permissions": dict(FIX_APP_PERMISSIONS),
        "default_events": [],
    }


async def start_fix_app(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    availability: Availability,
    github_organization: str | None,
    now: datetime,
) -> GitHubSetupStarted:
    _refuse_where_unavailable(availability)
    if github_organization is not None and not is_github_account(github_organization):
        raise InvalidRequestError(_ORGANIZATION_NAME)
    await queries.retire_abandoned_setups(session, org_id=org_id, now=now, kind=FIX_APP_KIND)
    if await queries.live_github_connection(session, org_id=org_id, kind=FIX_APP_KIND):
        raise ConflictError(_ALREADY)
    state = secrets.token_urlsafe(32)
    row = Connection(
        org_id=org_id,
        kind=FIX_APP_KIND,
        state=SETTING_UP,
        setup_state_hash=hash_of_state(state),
        setup_expires_at=now + SETUP_LIFETIME,
        setup_user_id=actor_user_id,
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError as raced:
        raise ConflictError(_ALREADY) from raced
    audit.record_connection_event(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=audit.CONNECTION_CREATED,
        connection_id=row.id,
        kind=FIX_APP_KIND,
    )
    slug = await queries.organization_slug(session, org_id=org_id)
    post_url = (
        "https://github.com/settings/apps/new"
        if github_organization is None
        else f"https://github.com/organizations/{github_organization}/settings/apps/new"
    )
    return GitHubSetupStarted(
        connection_id=row.id,
        state=state,
        manifest=fix_app_manifest(
            base_url=availability.app_base_url.rstrip("/"), organization_slug=slug
        ),
        post_url=post_url,
    )


async def complete_fix_app(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    state: str,
    code: str,
    keys: ConfiguredKeys,
    availability: Availability,
    now: datetime,
) -> GitHubSetupCompleted:
    """GitHub's redirect back: the `state` must be this organization's, unused, issued to
    this person, inside its hour; the code is sealed and its job added together."""
    _refuse_where_unavailable(availability)
    if not is_manifest_code(code):
        raise InvalidRequestError(_INVALID_SETUP, reason="invalid")
    row = await queries.lock_setup(
        session, org_id=org_id, state_hash=hash_of_state(state), kind=FIX_APP_KIND
    )
    if row is None or row.setup_user_id != actor_user_id:
        raise InvalidRequestError(_INVALID_SETUP, reason="invalid")
    expires_at = row.setup_expires_at
    if expires_at is None or expires_at <= now:
        row.state, row.setup_state_hash = DISCONNECTED, None
        await session.commit()
        raise InvalidRequestError(_EXPIRED_SETUP, reason="expired")
    owner = connection_secret_owner(str(org_id), str(row.id))
    row.sealed_manifest_code = sealed_box(keys, CONNECTION_MANIFEST_CODE_PURPOSE).seal(
        code, owner=owner
    )
    row.setup_state_hash = None
    await jobs.add(
        session,
        JobRequest(
            task=CONVERT_FIX_APP_TASK,
            queue=CONNECTIONS_INTERACTIVE_QUEUE,
            kwargs=connection_job_arguments(org_id=str(org_id), connection_id=str(row.id)),
            org_id=str(org_id),
            expires_seconds=(expires_at - now).total_seconds(),
            max_attempts=CONVERSION_ATTEMPTS,
        ),
    )
    return GitHubSetupCompleted(connection_id=row.id)


async def refresh_fix_app(
    session: AsyncSession, *, org_id: uuid.UUID, availability: Availability
) -> None:
    """Read again which installation, repositories and permissions the App has."""
    _refuse_where_unavailable(availability)
    row = await queries.live_github_connection(session, org_id=org_id, kind=FIX_APP_KIND)
    if row is None or row.github_app_id is None:
        raise NotFoundError(_NOT_SET_UP)
    await jobs.add(
        session,
        JobRequest(
            task=REFRESH_FIX_APP_TASK,
            queue=CONNECTIONS_INTERACTIVE_QUEUE,
            kwargs=connection_job_arguments(org_id=str(org_id), connection_id=str(row.id)),
            org_id=str(org_id),
            expires_seconds=TEST_EXPIRY_SECONDS,
        ),
    )


async def view(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    support_view: bool,
    availability: Availability,
    now: datetime,
) -> FixAppView:
    """The App's panel (design, *`/settings/connections`*). A reachability not yet known
    is null, never "unreachable"; a support view reads the state alone."""
    if availability.unavailable_reason is not None:
        return FixAppView(
            available=False,
            state=None,
            setup_step=None,
            install_url=None,
            permissions=None,
            granted_at=None,
            connection_id=None,
            repositories=[],
        )
    row = await queries.live_github_connection(session, org_id=org_id, kind=FIX_APP_KIND)
    if row is None or support_view:
        return FixAppView(
            available=True,
            state=None if row is None else row.state,
            setup_step=None,
            install_url=None,
            permissions=None,
            granted_at=None,
            connection_id=None if row is None else row.id,
            repositories=[],
        )
    places = await queries.places_of(session, org_id=org_id, connection_ids=[row.id])
    installed = row.github_installation_id is not None
    return FixAppView(
        available=True,
        state=row.state,
        setup_step=setup_step(row, now),
        install_url=None
        if row.github_app_slug is None or installed
        else f"https://github.com/apps/{row.github_app_slug}/installations/new",
        permissions=row.granted_permissions,
        granted_at=row.granted_at,
        connection_id=row.id,
        repositories=[
            FixAppRepository(
                id=place.id,
                key=place.key,
                is_private=place.is_private,
                is_reachable=place.is_reachable,
                public_allowed_at=place.public_allowed_at,
            )
            for place in places
        ],
    )
