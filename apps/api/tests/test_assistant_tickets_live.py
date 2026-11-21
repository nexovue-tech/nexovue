"""F4 plan T2.1, T2.2: the question ticket on the read door (AC7, AC8, AC11, AC12, AC53,
AC105).

`get_current_reader` is driven with a real request scope whose `route` is the application's
own matched route, so "allowed by handler" is proved on the real handlers: every catalog
route passes (AC53), and `GET /llm/calls` and `GET /findings/stats`, both on the read
surface, are refused. A test that saw only refusals would pass while everything was
refused, so the passing case is parametrized over the whole catalog.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import environment  # noqa: F401 — sets what importing the app reads
import pytest
from assistant_live import AVAILABLE, KEYS, NOW, chat_on, db, require_postgres, tenant
from fastapi.routing import APIRoute
from nexovue_platform.assistant_records import TICKET_PURPOSE
from nexovue_platform.assistant_tools import TICKET_HANDLERS
from nexovue_platform.checks.sealing import SealedBox, keyring_from
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from nexovue_api.apitokens.tokens import TICKETS_ARE_NOT_PEOPLE, TICKETS_NOT_ACCEPTED
from nexovue_api.assistant import service, tickets
from nexovue_api.assistant.schemas import AskContext
from nexovue_api.auth.dependencies import Identity, Reader, get_current_reader
from nexovue_api.data.postgres.models import (
    AIProjectOptOut,
    AssistantMessage,
    AssistantSettings,
    Project,
    User,
)
from nexovue_api.deps import get_current_session
from nexovue_api.errors.exceptions import PermissionDeniedError, UnauthenticatedError
from nexovue_api.main import app
from nexovue_api.settings import API_PREFIX

require_postgres("the question ticket suite")
__all__ = ["db"]

SETTINGS: Any = app.state.settings.model_copy(
    update={"ai_secret_key": KEYS.current, "nexovue_deployment": "self-hosted"}
)


@pytest.fixture(autouse=True)
def _self_hosted_with_an_ai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ticket check reads the application's settings: here an install that offers AI."""
    monkeypatch.setattr(app.state, "settings", SETTINGS)


def _routes() -> list[APIRoute]:
    """Every API route as routing hands it on: FastAPI sets `scope["route"]` to the original
    route of an included router's matched context (`fastapi/routing.py`, `_handle_selected`)."""
    found: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            found.append(route)
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is not None:
            found.extend(
                context.original_route
                for context in contexts()
                if isinstance(context.original_route, APIRoute)
            )
    return found


def _route(handler: str, method: str = "GET") -> APIRoute:
    """The application's own route for a handler, as routing would match it."""
    for route in _routes():
        key = f"{route.endpoint.__module__.replace('.', '/')}.py:{route.endpoint.__name__}"
        if key == handler and method in (route.methods or set()):
            return route
    raise AssertionError(f"no {method} route serves {handler}")


def _request(
    ticket: str, *, handler: str, method: str = "GET", kind: str = "http", checked: bool = True
) -> Request:
    route = _route(handler)
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": f"{API_PREFIX}{route.path}",
            "headers": [(b"authorization", f"Bearer {ticket}".encode())],
            "query_string": b"",
            "route": route,
            "app": app if checked else None,
        }
    )
    if kind == "websocket":
        # A WebSocket scope has no method; the reader must assume none (design, section 1).
        request.scope["type"] = "websocket"
        request.scope.pop("method")
    return request


class _NoSessions:
    async def get(self, token: str) -> None:
        raise AssertionError("a ticket request looked for a session")

    peek = get


SEARCH = "nexovue_api/logs/search.py:search"


async def _asked(db: AsyncSession, **changes: Any) -> tuple[str, AssistantMessage]:
    """A stored question, through the real `ask`, and its ticket as the worker would open it."""
    made = await tenant(db)
    await chat_on(db, made)
    view = await service.ask(
        db,
        identity=_identity(made),
        project_id=made.project.id,
        conversation_id=None,
        question="What is failing right now?",
        context=AskContext(),
        ai=AVAILABLE,
        keys=KEYS,
        now=datetime.now(UTC),
    )
    message = await db.get(AssistantMessage, view.id)
    assert message is not None and message.sealed_ticket is not None
    ticket = _opened(message)
    for name, value in changes.items():
        setattr(message, name, value)
    await db.flush()
    return ticket, message


def _identity(made: Any) -> Identity:
    return Identity(user=made.user, organization=made.organization)


def _opened(message: AssistantMessage) -> str:
    box = SealedBox(keyring_from(KEYS.current, KEYS.previous), purpose=TICKET_PURPOSE)
    assert message.sealed_ticket is not None
    return box.open(message.sealed_ticket, owner=str(message.id))


async def _read(db: AsyncSession, ticket: str, **request: Any) -> Reader:
    return await get_current_reader(
        _request(ticket, handler=request.pop("handler", SEARCH), **request),
        db,
        _NoSessions(),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("handler", sorted(TICKET_HANDLERS))
async def test_every_catalog_route_reads_as_the_question_s_project(
    db: AsyncSession, handler: str
) -> None:
    """AC53, AC105: neither a person nor a token; scoped to the question's project."""
    ticket, message = await _asked(db)

    reader = await _read(db, ticket, handler=handler)

    assert (reader.org_id, reader.project_scope, reader.message_id) == (
        message.org_id,
        message.project_id,
        message.id,
    )
    assert not reader.is_person and not reader.is_token and not reader.is_real_person
    assert not reader.may_read_project(uuid.uuid4())


@pytest.mark.parametrize(
    "handler",
    [
        "nexovue_api/llm_observability/router.py:list_llm_calls",
        "nexovue_api/findings/router.py:finding_stats",
        # A finding's case timeline waits for Step 7; a target is not chat's at all.
        "nexovue_api/findings/router.py:read_timeline",
        "nexovue_api/slos/router.py:list_slos",
    ],
)
async def test_a_read_surface_route_outside_the_catalog_is_refused(
    db: AsyncSession, handler: str
) -> None:
    """AC7: the read surface is wider than chat."""
    ticket, _ = await _asked(db)

    with pytest.raises(PermissionDeniedError, match=tickets.TICKET_NOT_HERE):
        await _read(db, ticket, handler=handler)


async def test_anything_but_an_http_get_is_refused(db: AsyncSession) -> None:
    ticket, _ = await _asked(db)

    with pytest.raises(PermissionDeniedError, match=tickets.TICKET_READS_ONLY):
        await _read(db, ticket, method="POST")
    with pytest.raises(PermissionDeniedError, match=tickets.TICKET_READS_ONLY):
        await _read(db, ticket, kind="websocket")


async def test_an_unknown_ticket_is_refused(db: AsyncSession) -> None:
    with pytest.raises(UnauthenticatedError, match=tickets.TICKET_UNKNOWN):
        await _read(db, tickets.mint())


@pytest.mark.parametrize(
    "ending",
    [
        {"state": "answered", "ticket_hash": None, "sealed_ticket": None},
        {"deadline_at": NOW - timedelta(days=1)},
    ],
)
async def test_a_question_that_ended_ends_its_ticket(
    db: AsyncSession, ending: dict[str, Any]
) -> None:
    """AC11: Stop, death and the deadline all end it, with no revocation row."""
    ticket, message = await _asked(db)
    if "state" in ending:
        # As the finish function leaves it; the ticket's hash no longer finds the row.
        message.state, message.ticket_hash, message.sealed_ticket = "answered", None, None
        message.phase = None
    else:
        message.deadline_at = ending["deadline_at"]
    await db.flush()

    with pytest.raises(UnauthenticatedError):
        await _read(db, ticket)


@pytest.mark.parametrize("change", ["removed", "signed_out_everywhere", "now_a_viewer"])
async def test_a_person_who_can_no_longer_ask_ends_the_ticket(
    db: AsyncSession, change: str
) -> None:
    ticket, message = await _asked(db)
    changes: dict[str, dict[str, Any]] = {
        "removed": {"removed_at": datetime.now(UTC)},
        "signed_out_everywhere": {"sessions_valid_from": datetime.now(UTC) + timedelta(minutes=1)},
        "now_a_viewer": {"role": "viewer"},
    }
    values = changes[change]
    await db.execute(update(User).where(User.id == message.user_id).values(**values))

    with pytest.raises(PermissionDeniedError, match=tickets.TICKET_PERSON_GONE):
        await _read(db, ticket)


async def test_a_deleted_project_ends_the_ticket(db: AsyncSession) -> None:
    ticket, message = await _asked(db)
    await db.execute(
        update(Project).where(Project.id == message.project_id).values(deleted_at=datetime.now(UTC))
    )

    with pytest.raises(PermissionDeniedError, match=tickets.TICKET_SCOPE_GONE):
        await _read(db, ticket)


@pytest.mark.parametrize("change", ["chat_off", "data_level", "opted_out"])
async def test_chat_turned_off_or_moved_ends_the_ticket_on_its_next_read(
    db: AsyncSession, change: str
) -> None:
    """AC60: rechecked on every read."""
    ticket, message = await _asked(db)
    if change == "opted_out":
        db.add(AIProjectOptOut(org_id=message.org_id, project_id=message.project_id))
    else:
        values = {"enabled": False} if change == "chat_off" else {"data_level": "facts"}
        await db.execute(
            update(AssistantSettings)
            .where(AssistantSettings.org_id == message.org_id)
            .values(**values)
        )
    await db.flush()

    with pytest.raises(PermissionDeniedError, match=tickets.TICKET_CHAT_OFF):
        await _read(db, ticket)


async def test_a_server_that_registered_no_check_refuses_tickets(db: AsyncSession) -> None:
    ticket, _ = await _asked(db)

    with pytest.raises(UnauthenticatedError, match=TICKETS_NOT_ACCEPTED):
        await _read(db, ticket, checked=False)


async def test_a_ticket_on_a_person_s_route_is_told_it_is_not_a_person() -> None:
    """Design, trap 6: a clear 403, not "your session has expired"."""
    with pytest.raises(PermissionDeniedError, match=TICKETS_ARE_NOT_PEOPLE):
        await get_current_session(
            _request(tickets.mint(), handler=SEARCH),
            _NoSessions(),  # type: ignore[arg-type]
        )
