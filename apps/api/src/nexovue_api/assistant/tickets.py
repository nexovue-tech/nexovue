"""The question ticket: chat's read credential (F4 design, section 1; ADR-055).

`nvat_` plus 32 random characters, a prefix of its own beside `nvt_` (tokens) and `nvk_`
(ingest), so a credential says what it is before anything looks it up. **There is exactly
one ticket per question,** minted by the API in the same transaction as the message, and
stored on it: its SHA-256 for lookup, and the value sealed under `AI_SECRET_KEY` for the
worker. It is valid only while its message is `queued` or `running` and inside its
deadline, so Stop, death, deletion and retention all end it with no revocation row.

`valid_for` refuses, each in one sentence, and is run on **every** read: an unknown ticket;
a message no longer in flight, or past its deadline; anything but an HTTP GET; a handler
outside `TICKET_HANDLERS` (by the matched route's handler, never by path text, since a
nested router's path names no one route); a person removed, signed out everywhere since the
question, or without `ASK_ASSISTANT`; a deleted organization or project; and AI or chat
off, the project opted out, or chat's consent no longer matching.
"""

from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from datetime import UTC, datetime
from typing import Final

from nexovue_platform.assistant_records import IN_FLIGHT, TICKET_PURPOSE
from nexovue_platform.assistant_tools import TICKET_HANDLERS
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, keyring_from
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from nexovue_api.ai.service import Availability, availability_of
from nexovue_api.apitokens.tokens import ASSISTANT_TICKET_PREFIX
from nexovue_api.assistant.standing import standing_of
from nexovue_api.auth.dependencies import TicketScope
from nexovue_api.auth.permissions import Action, may
from nexovue_api.data.postgres.models import AssistantMessage, Organization, Project, User
from nexovue_api.errors.exceptions import PermissionDeniedError, UnauthenticatedError
from nexovue_api.settings import Settings

TICKET_PREFIX: Final = ASSISTANT_TICKET_PREFIX
TICKET_SECRET_LENGTH: Final = 32
TICKET_ALPHABET: Final = string.ascii_letters + string.digits

TICKET_UNKNOWN: Final = "This question ticket is not valid."
TICKET_ENDED: Final = "This question has ended, and its ticket with it."
TICKET_READS_ONLY: Final = "A question ticket reads; it never changes anything."
TICKET_NOT_HERE: Final = "A question ticket reaches only chat's own read routes."
TICKET_PERSON_GONE: Final = "The person who asked can no longer ask."
TICKET_SCOPE_GONE: Final = "The project or organization this question was about is gone."
TICKET_CHAT_OFF: Final = "Chat is no longer on for this project."


def mint() -> str:
    secret = "".join(secrets.choice(TICKET_ALPHABET) for _ in range(TICKET_SECRET_LENGTH))
    return f"{TICKET_PREFIX}{secret}"


def fingerprint(ticket: str) -> str:
    """SHA-256: a 32-character random secret leaves nothing for stretching to protect."""
    return hashlib.sha256(ticket.encode()).hexdigest()


def _box(keys: ConfiguredKeys) -> SealedBox:
    return SealedBox(keyring_from(keys.current, keys.previous), purpose=TICKET_PURPOSE)


def seal(ticket: str, *, message_id: uuid.UUID, keys: ConfiguredKeys) -> bytes:
    """Sealed for the worker, bound to its own message, so it opens for no other row."""
    return _box(keys).seal(ticket, owner=str(message_id))


def handler_key(connection: HTTPConnection) -> str | None:
    """The matched route's handler as `THE_SURFACE` names it: `<module path>:<name>`."""
    route = connection.scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    module = getattr(endpoint, "__module__", None)
    name = getattr(endpoint, "__name__", None)
    if not isinstance(module, str) or not isinstance(name, str):
        return None
    return f"{module.replace('.', '/')}.py:{name}"


def _refuse_connection(connection: HTTPConnection) -> None:
    # A WebSocket scope has no method, so nothing is assumed (design, section 1).
    if connection.scope.get("type") != "http" or connection.scope.get("method") != "GET":
        raise PermissionDeniedError(TICKET_READS_ONLY)
    if handler_key(connection) not in TICKET_HANDLERS:
        raise PermissionDeniedError(TICKET_NOT_HERE)


async def ticket_check(db: AsyncSession, presented: str, connection: HTTPConnection) -> TicketScope:
    """What `main.py` registers as the app's `ticket_check` (see `get_current_reader`). The
    settings are the application's own, as every dependency reads them."""
    settings: Settings = connection.app.state.settings
    message = await valid_for(
        db,
        presented,
        connection=connection,
        availability=availability_of(settings),
        now=datetime.now(UTC),
    )
    return TicketScope(org_id=message.org_id, project_id=message.project_id, message_id=message.id)


async def valid_for(
    db: AsyncSession,
    presented: str,
    *,
    connection: HTTPConnection,
    availability: Availability,
    now: datetime,
) -> AssistantMessage:
    """The message a ticket belongs to, when it may make this read now; else a refusal."""
    message: AssistantMessage | None = await db.scalar(
        select(AssistantMessage).where(AssistantMessage.ticket_hash == fingerprint(presented))
    )
    if message is None:
        raise UnauthenticatedError(TICKET_UNKNOWN)
    if message.state not in IN_FLIGHT or now >= message.deadline_at:
        raise UnauthenticatedError(TICKET_ENDED)
    _refuse_connection(connection)
    user: User | None = await db.scalar(
        select(User).where(User.id == message.user_id, User.org_id == message.org_id)
    )
    if (
        user is None
        or user.removed_at is not None
        or (user.sessions_valid_from is not None and user.sessions_valid_from > message.created_at)
        or not may(user.role, Action.ASK_ASSISTANT)
    ):
        raise PermissionDeniedError(TICKET_PERSON_GONE)
    live = await db.scalar(
        select(Project.id)
        .join(Organization, Organization.id == Project.org_id)
        .where(
            Project.id == message.project_id,
            Project.org_id == message.org_id,
            Project.deleted_at.is_(None),
            Organization.deleted_at.is_(None),
        )
    )
    if live is None:
        raise PermissionDeniedError(TICKET_SCOPE_GONE)
    standing = await standing_of(
        db, org_id=message.org_id, project_id=message.project_id, availability=availability
    )
    if not standing.ready:
        raise PermissionDeniedError(TICKET_CHAT_OFF)
    return message
