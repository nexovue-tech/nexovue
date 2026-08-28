"""Sign in, sign out, and who am I.

HTTP only. The cookie is set and cleared here because it is a transport
concern; the decision about whether the credentials were right belongs to
`service.py` and the token itself belongs to the session store.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Request, Response

from nexovue_api.auth import service
from nexovue_api.auth.cookies import clear_session_cookie, set_session_cookie
from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.schemas import IdentityResponse, LoginRequest
from nexovue_api.auth.sessions import session_token_from_headers
from nexovue_api.deps import AppSettings, DbSession, SessionStoreDep

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", summary="Exchange credentials for a session")
async def login(
    payload: LoginRequest,
    response: Response,
    db: DbSession,
    sessions: SessionStoreDep,
    settings: AppSettings,
) -> IdentityResponse:
    user, organization = await service.authenticate(
        db, email=payload.email, password=payload.password
    )
    token = await service.start_session(db, sessions, user=user)
    set_session_cookie(response, token, settings)
    return service.describe_identity(user, organization)


@router.post(
    "/logout",
    status_code=HTTPStatus.NO_CONTENT,
    summary="End the current session",
)
async def logout(
    request: Request,
    response: Response,
    db: DbSession,
    sessions: SessionStoreDep,
    settings: AppSettings,
) -> None:
    """Idempotent: a request with no session, or a stale one, still returns 204."""
    await service.end_session(db, sessions, session_token_from_headers(request.headers))
    clear_session_cookie(response, settings)


@router.get("/me", summary="The signed-in user and their organization")
async def me(identity: CurrentIdentity) -> IdentityResponse:
    return service.describe_identity(identity.user, identity.organization)
