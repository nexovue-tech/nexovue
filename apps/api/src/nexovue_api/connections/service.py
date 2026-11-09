"""Tracker connections: what the routes do (F22 plan T3.5, T3.8, T3.11; design
*Connecting GitHub*).

**Setting up a GitHub App** is three steps across GitHub's pages. `start_github` makes the
row, issues a one-time `state` (only its hash is stored) and answers the manifest the
console posts to GitHub. `complete_github` takes GitHub's redirect back: the `state` must
be this organization's, unused, issued to this person, inside its hour; the code is then
sealed and its conversion job added in the same transaction. The redirect's
`installation_id` is never read (AC38): a job asks GitHub which installation is the
App's.

**Nothing here calls GitHub** (AC32). A test, a refresh and a replaced key each add a job.
Each statement binds the caller's organization, and a connection elsewhere is the same
404 as none at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, keyring_from
from nexovue_platform.connections import (
    CHECK_CONNECTION_TASK,
    CONNECTION_CREDENTIAL_PURPOSE,
    CONNECTION_MANIFEST_CODE_PURPOSE,
    CONNECTION_TEST_RESULT_KIND,
    CONNECTIONS_INTERACTIVE_QUEUE,
    CONVERT_MANIFEST_TASK,
    DISCONNECTED,
    GITHUB,
    REFRESH_CONNECTION_TASK,
    SETTING_UP,
    connection_job_arguments,
    connection_secret_owner,
    connection_test_arguments,
    is_github_account,
    is_manifest_code,
)
from nexovue_platform.jobs import JobRequest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.connections import jobs, queries
from nexovue_api.connections.schemas import (
    MAX_PRIVATE_KEY_CHARS,
    Availability,
    ConnectionPlace,
    ConnectionsOverview,
    ConnectionTestAccepted,
    ConnectionTestResult,
    ConnectionView,
    GitHubSetupCompleted,
    GitHubSetupStarted,
    SetupStep,
)
from nexovue_api.data.postgres.models import Connection, ConnectionRepository
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
    NotFoundError,
)

# GitHub's manifest code, and so this setup, lasts an hour (its manifest page).
SETUP_LIFETIME: Final = timedelta(hours=1)
# GitHub caps an App's name at 34 characters (general knowledge, not read from its pages).
APP_NAME_LIMIT: Final = 34
APP_NAME_PREFIX: Final = "nexovue-"
# The console routes GitHub returns a person to (`app/router.tsx`).
RETURN_PATH: Final = "/settings/connections/github/return"
INSTALLED_PATH: Final = "/settings/connections/github/installed"
WEBHOOK_PATH: Final = "/api/v1/connections/webhook/{connection_id}"
# F22 needs issues, and metadata to read a repository at all (design, *Tokens*).
APP_PERMISSIONS: Final = {"issues": "write", "metadata": "read"}
APP_EVENTS: Final = ("issues",)
# A conversion GitHub could not answer is retried by its job inside the code's hour.
CONVERSION_ATTEMPTS: Final = 3
# A test nobody picks up within this is dropped, as a channel's send test is.
TEST_EXPIRY_SECONDS: Final = 60
TEST_POLL_SECONDS: Final = 0.5
MAX_TEST_WAIT_SECONDS: Final = 10

_INVALID_SETUP: Final = "This setup link is not valid. Nothing was changed."
_EXPIRED_SETUP: Final = "This setup link has expired. Nothing was changed."
_ALREADY_CONNECTED: Final = (
    "GitHub is already connected, or a setup is in progress. Finish it, or stop it first."
)
_NOTHING_TO_TEST: Final = "This connection has no App to test yet. Finish setting it up first."
_NOT_CONNECTED: Final = "GitHub is not connected yet."
_NOT_A_KEY: Final = "This is not a GitHub App private key. Upload the .pem file GitHub gave you."
_NO_SUCH_CONNECTION: Final = "No such connection."
_ORGANIZATION_NAME: Final = "That is not a GitHub organization name."
_PEM: Final = re.compile(
    r"-----BEGIN (RSA )?PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+\n-----END (RSA )?PRIVATE KEY-----\n?"
)

DisconnectHook = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[None]]
# Called inside a disconnect's transaction (design, *API*): F5 registers one for its App.
DISCONNECT_HOOKS: list[DisconnectHook] = []


def register_disconnect_hook(hook: DisconnectHook) -> None:
    DISCONNECT_HOOKS.append(hook)


def _box(keys: ConfiguredKeys, purpose: str) -> SealedBox:
    if not keys.current:
        raise DependencyUnavailableError(
            "Tracker connections cannot be saved: CONNECTION_SECRET_KEY is not set."
        )
    return SealedBox(keyring_from(keys.current, keys.previous), purpose=purpose)


def _state_hash(state: str) -> bytes:
    return hashlib.sha256(state.encode()).digest()


def _refuse_where_unavailable(availability: Availability) -> None:
    if availability.refusal is not None:
        raise ConflictError(availability.refusal)


# ----------------------------------------------------------------- the list


async def overview(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    support_view: bool,
    availability: Availability,
    email_available: bool,
    now: datetime | None = None,
) -> ConnectionsOverview:
    """Every live connection, with what a person may see of it (design, *API*)."""
    reason = availability.unavailable_reason
    if reason is not None:
        return ConnectionsOverview(
            available=False,
            unavailable_reason=reason,
            email_available=email_available,
            connections=[],
        )
    rows = await queries.live_connections(session, org_id=org_id)
    places = await queries.places_of(
        session, org_id=org_id, connection_ids=[row.id for row in rows]
    )
    moment = now or datetime.now(UTC)
    return ConnectionsOverview(
        available=True,
        unavailable_reason=None,
        email_available=email_available,
        connections=[
            ConnectionView(id=row.id, kind=row.kind, state=row.state)
            if support_view
            else _view(row, [place for place in places if place.connection_id == row.id], moment)
            for row in rows
        ],
    )


def _view(row: Connection, places: list[ConnectionRepository], now: datetime) -> ConnectionView:
    installed = row.github_installation_id is not None
    return ConnectionView(
        id=row.id,
        kind=row.kind,
        state=row.state,
        app_slug=row.github_app_slug,
        install_url=None
        if row.github_app_slug is None or installed
        else f"https://github.com/apps/{row.github_app_slug}/installations/new",
        setup_step=setup_step(row, now),
        setup_expires_at=row.setup_expires_at if row.state == SETTING_UP else None,
        last_ok_at=row.last_ok_at,
        failing_since=row.failing_since,
        last_failure=row.last_failure,
        last_failure_status=row.last_failure_status,
        repositories=[
            ConnectionPlace(
                id=place.id,
                key=place.key,
                is_private=place.is_private,
                is_reachable=place.is_reachable,
            )
            for place in places
        ],
    )


def setup_step(row: Connection, now: datetime) -> SetupStep | None:
    """Which row of the setup table a connection in `setting_up` is on."""
    if row.state != SETTING_UP:
        return None
    if row.github_app_id is None:
        if row.last_failure == "manifest_refused":
            return "refused"
        if row.sealed_manifest_code is not None:
            return "converting"
        expired = row.setup_expires_at is not None and row.setup_expires_at <= now
        return "expired" if expired or row.last_failure == "manifest_expired" else "on_github"
    if row.github_installation_id is None:
        return "not_installed"
    if row.last_failure == "no_repository":
        return "no_repository"
    return "test_failed" if row.last_failure is not None else "testing"


# ------------------------------------------------------------ setting up


async def start_github(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    availability: Availability,
    reachable: bool,
    github_organization: str | None,
    now: datetime,
) -> GitHubSetupStarted:
    _refuse_where_unavailable(availability)
    if github_organization is not None and not is_github_account(github_organization):
        raise InvalidRequestError(_ORGANIZATION_NAME)
    await queries.retire_abandoned_setups(session, org_id=org_id, now=now)
    if await queries.live_github_connection(session, org_id=org_id) is not None:
        raise ConflictError(_ALREADY_CONNECTED)
    state = secrets.token_urlsafe(32)
    row = Connection(
        org_id=org_id,
        kind=GITHUB,
        state=SETTING_UP,
        setup_state_hash=_state_hash(state),
        setup_expires_at=now + SETUP_LIFETIME,
        setup_user_id=actor_user_id,
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError as raced:
        # Another admin started one at the same moment; the one-live-row index chose.
        raise ConflictError(_ALREADY_CONNECTED) from raced
    audit.record_connection_event(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=audit.CONNECTION_CREATED,
        connection_id=row.id,
        kind=GITHUB,
    )
    slug = await queries.organization_slug(session, org_id=org_id)
    return GitHubSetupStarted(
        connection_id=row.id,
        state=state,
        manifest=github_manifest(
            base_url=availability.app_base_url.rstrip("/"),
            connection_id=row.id,
            organization_slug=slug,
            reachable=reachable,
        ),
        post_url=_post_url(github_organization),
    )


def github_manifest(
    *, base_url: str, connection_id: uuid.UUID, organization_slug: str, reachable: bool
) -> dict[str, object]:
    """The private App GitHub is asked to create (design, *Connecting GitHub*, step 2).

    Unverified until the founder's real run (T3.0): whether GitHub accepts a manifest
    with `hook_attributes.active` false, and whether `installation` events need asking
    for. Polling and the hourly refresh carry everything either way."""
    return {
        "name": f"{APP_NAME_PREFIX}{organization_slug}"[:APP_NAME_LIMIT],
        "url": base_url,
        "hook_attributes": {
            "url": base_url + WEBHOOK_PATH.format(connection_id=connection_id),
            # An install GitHub cannot reach asks for no deliveries at all.
            "active": reachable,
        },
        "redirect_url": base_url + RETURN_PATH,
        "setup_url": base_url + INSTALLED_PATH,
        "setup_on_update": True,
        "public": False,
        "default_permissions": dict(APP_PERMISSIONS),
        "default_events": list(APP_EVENTS),
    }


def _post_url(github_organization: str | None) -> str:
    if github_organization is None:
        return "https://github.com/settings/apps/new"
    return f"https://github.com/organizations/{github_organization}/settings/apps/new"


async def complete_github(
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
    """GitHub's redirect back. A wrong, reused or someone else's `state` changes
    nothing; one past its hour frees the slot (AC74)."""
    _refuse_where_unavailable(availability)
    if not is_manifest_code(code):
        raise InvalidRequestError(_INVALID_SETUP, reason="invalid")
    row = await queries.lock_setup(session, org_id=org_id, state_hash=_state_hash(state))
    if row is None or row.setup_user_id != actor_user_id:
        raise InvalidRequestError(_INVALID_SETUP, reason="invalid")
    expires_at = row.setup_expires_at
    if expires_at is None or expires_at <= now:
        row.state, row.setup_state_hash = DISCONNECTED, None
        # Kept although the request is refused: the slot must be free for a new setup.
        await session.commit()
        raise InvalidRequestError(_EXPIRED_SETUP, reason="expired")
    owner = connection_secret_owner(str(org_id), str(row.id))
    row.sealed_manifest_code = _box(keys, CONNECTION_MANIFEST_CODE_PURPOSE).seal(code, owner=owner)
    row.setup_state_hash = None
    await jobs.add(
        session,
        JobRequest(
            task=CONVERT_MANIFEST_TASK,
            queue=CONNECTIONS_INTERACTIVE_QUEUE,
            kwargs=connection_job_arguments(org_id=str(org_id), connection_id=str(row.id)),
            org_id=str(org_id),
            expires_seconds=(expires_at - now).total_seconds(),
            max_attempts=CONVERSION_ATTEMPTS,
        ),
    )
    return GitHubSetupCompleted(connection_id=row.id)


async def refresh_github(
    session: AsyncSession, *, org_id: uuid.UUID, availability: Availability
) -> None:
    """Read again which installation and repositories the App has: after GitHub's
    install page returns a person, and whenever they ask."""
    _refuse_where_unavailable(availability)
    row = await queries.live_github_connection(session, org_id=org_id)
    if row is None or row.github_app_id is None:
        raise NotFoundError(_NOT_CONNECTED)
    await jobs.add(
        session,
        JobRequest(
            task=REFRESH_CONNECTION_TASK,
            queue=CONNECTIONS_INTERACTIVE_QUEUE,
            kwargs=connection_job_arguments(org_id=str(org_id), connection_id=str(row.id)),
            org_id=str(org_id),
            expires_seconds=TEST_EXPIRY_SECONDS,
        ),
    )


# ------------------------------------------------------------ testing


async def dispatch_test(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    availability: Availability,
) -> ConnectionTestAccepted:
    _refuse_where_unavailable(availability)
    row = await queries.lock_live_connection(session, org_id=org_id, connection_id=connection_id)
    if row is None:
        raise NotFoundError(_NO_SUCH_CONNECTION)
    if row.sealed_credential is None:
        raise ConflictError(_NOTHING_TO_TEST)
    return await _add_test(session, org_id=org_id, connection_id=row.id)


async def _add_test(
    session: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> ConnectionTestAccepted:
    request_id = str(uuid.uuid4())
    await jobs.add(
        session,
        JobRequest(
            task=CHECK_CONNECTION_TASK,
            queue=CONNECTIONS_INTERACTIVE_QUEUE,
            kwargs=connection_test_arguments(
                org_id=str(org_id), connection_id=str(connection_id), request_id=request_id
            ),
            org_id=str(org_id),
            expires_seconds=TEST_EXPIRY_SECONDS,
        ),
    )
    return ConnectionTestAccepted(request_id=request_id)


async def test_result(
    session: AsyncSession, *, org_id: uuid.UUID, request_id: str, wait_seconds: float = 0
) -> ConnectionTestResult:
    """The outcome, waiting up to `wait_seconds` for it, as a send test does. A request
    id that is not a UUID was never issued, and one from elsewhere finds nothing."""
    try:
        request = uuid.UUID(request_id)
    except ValueError:
        return ConnectionTestResult(pending=True)
    deadline = time.monotonic() + min(wait_seconds, MAX_TEST_WAIT_SECONDS)
    while True:
        payload = await queries.test_outcome(
            session, org_id=org_id, request_id=request, kind=CONNECTION_TEST_RESULT_KIND
        )
        if payload is not None:
            return _test_result(payload)
        if time.monotonic() >= deadline:
            return ConnectionTestResult(pending=True)
        # The read changed nothing; ending its transaction keeps the wait from holding a
        # connection idle in one. A commit, not a rollback: sessions keep their objects on
        # commit (`expire_on_commit=False`), and a rollback would expire the caller's identity.
        await session.commit()
        await asyncio.sleep(TEST_POLL_SECONDS)


def _test_result(payload: dict[str, object]) -> ConnectionTestResult:
    ok, failure, state = payload.get("ok"), payload.get("failure"), payload.get("state")
    return ConnectionTestResult(
        pending=False,
        ok=ok if isinstance(ok, bool) else None,
        failure=failure if isinstance(failure, str) else None,
        state=state if isinstance(state, str) else None,
    )


# ---------------------------------------------------- replacing and disconnecting


async def replace_credential(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    connection_id: uuid.UUID,
    private_key: str,
    keys: ConfiguredKeys,
    availability: Availability,
) -> ConnectionTestAccepted:
    """FR43: re-seal a new App key in place, keep every place and link, and test it."""
    _refuse_where_unavailable(availability)
    key = private_key.strip() + "\n"
    if len(key) > MAX_PRIVATE_KEY_CHARS or _PEM.fullmatch(key) is None:
        raise InvalidRequestError(_NOT_A_KEY)
    row = await queries.lock_live_connection(session, org_id=org_id, connection_id=connection_id)
    if row is None or row.kind != GITHUB:
        raise NotFoundError(_NO_SUCH_CONNECTION)
    owner = connection_secret_owner(str(org_id), str(row.id))
    row.sealed_credential = _box(keys, CONNECTION_CREDENTIAL_PURPOSE).seal(key, owner=owner)
    audit.record_connection_event(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=audit.CONNECTION_CREDENTIAL_REPLACED,
        connection_id=row.id,
        kind=row.kind,
    )
    return await _add_test(session, org_id=org_id, connection_id=row.id)


async def disconnect(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    connection_id: uuid.UUID,
    hooks: tuple[DisconnectHook, ...],
) -> None:
    """FR32: every stored secret goes in this transaction. The App stays in the
    customer's GitHub account until they delete it there."""
    row = await queries.lock_live_connection(session, org_id=org_id, connection_id=connection_id)
    if row is None:
        raise NotFoundError(_NO_SUCH_CONNECTION)
    row.state = DISCONNECTED
    row.sealed_credential = row.sealed_webhook_secret = row.sealed_manifest_code = None
    row.setup_state_hash = None
    row.sync_lease_until = None
    for hook in hooks:
        await hook(session, org_id, row.id)
    audit.record_connection_event(
        session,
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=audit.CONNECTION_DELETED,
        connection_id=row.id,
        kind=row.kind,
    )
