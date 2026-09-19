"""Who is calling the admin app: a staff member with a live session, or nobody.

The staff cookie only (ADR-016). A bearer header is not read here, so a customer
token cannot even be presented; and the customer app never reads this cookie.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends
from starlette.requests import HTTPConnection

from nexovue_api.data.postgres.models import StaffAccount
from nexovue_api.deps import DbSession, RedisClient
from nexovue_api.errors.exceptions import UnauthenticatedError
from nexovue_api.staff.sessions import STAFF_COOKIE_NAME, StaffSessionStore

NOT_SIGNED_IN: Final = "Sign in to the admin console."


def get_staff_sessions(redis: RedisClient) -> StaffSessionStore:
    return StaffSessionStore(redis)


StaffSessions = Annotated[StaffSessionStore, Depends(get_staff_sessions)]


async def get_current_staff(
    connection: HTTPConnection, db: DbSession, sessions: StaffSessions
) -> StaffAccount:
    token = connection.cookies.get(STAFF_COOKIE_NAME)
    session = await sessions.get(token) if token else None
    if session is None:
        raise UnauthenticatedError(NOT_SIGNED_IN)
    account = await db.get(StaffAccount, session.staff_id)
    if account is None or account.disabled_at is not None:
        raise UnauthenticatedError(NOT_SIGNED_IN)
    return account


CurrentStaff = Annotated[StaffAccount, Depends(get_current_staff)]
