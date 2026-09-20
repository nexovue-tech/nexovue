"""Support access (F31). HTTP only.

`/support-access` is an owner's: whether nexovue support may view as their
people, and every time it has. `/support/view-as` spends the single-use ticket
the admin app issued and opens a read-only customer session with it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse

from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.cookies import set_session_cookie
from nexovue_api.auth.policy import MayGrantSupport
from nexovue_api.auth.sessions import Impersonation
from nexovue_api.deps import AppSettings, DbSession, RedisClient, SessionStoreDep
from nexovue_api.errors.exceptions import UnauthenticatedError
from nexovue_api.support import service
from nexovue_api.support.schemas import GrantSupportAccess, SupportAccessState
from nexovue_api.support.tickets import TicketStore

TICKET_SPENT: Final = "This support link has already been used, or has expired."

router = APIRouter(tags=["support"])


def get_tickets(redis: RedisClient) -> TicketStore:
    return TicketStore(redis)


Tickets = Annotated[TicketStore, Depends(get_tickets)]


@router.get("/support-access", summary="Whether nexovue support may view as your people")
async def read_support_access(identity: MayGrantSupport, db: DbSession) -> SupportAccessState:
    return await service.state(db, org_id=identity.org_id, now=datetime.now(UTC))


@router.post(
    "/support-access",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Let nexovue support view as your people, for a time",
)
async def grant_support_access(
    identity: MayGrantSupport, db: DbSession, request: GrantSupportAccess
) -> None:
    await service.grant(db, owner=identity.user, hours=request.hours, now=datetime.now(UTC))


@router.delete(
    "/support-access",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw support access; any support view ends on its next request",
)
async def withdraw_support_access(identity: MayGrantSupport, db: DbSession) -> None:
    await service.withdraw(db, owner=identity.user, now=datetime.now(UTC))


@router.get("/support/view-as", summary="Open a support view from a single-use ticket")
async def redeem_view_as(
    ticket: Annotated[str, Query(min_length=1, max_length=512)],
    tickets: Tickets,
    sessions: SessionStoreDep,
    settings: AppSettings,
) -> RedirectResponse:
    spent = await tickets.spend(ticket)
    if spent is None:
        raise UnauthenticatedError(TICKET_SPENT)
    token, _ = await sessions.create(
        user_id=str(spent.user_id),
        org_id=str(spent.org_id),
        client=ClientDescription(label=f"nexovue support — {spent.operator_label}", network=None),
        impersonation=Impersonation(
            impersonation_id=str(spent.impersonation_id),
            operator_label=spent.operator_label,
            reason=spent.reason,
            grant_id=str(spent.grant_id),
            until=spent.until,
        ),
    )
    # To the console's home, as the person being viewed; the cookie is the session.
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, token, settings)
    return response
