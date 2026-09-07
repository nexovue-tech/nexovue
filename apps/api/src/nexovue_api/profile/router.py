"""The account of whoever is signed in. HTTP only.

No route takes a user id. `CurrentIdentity` names the account, so there is no
value in a path or a body that could point these anywhere else.

**No `GET`.** `/auth/me` already answers "who is signed in" and the screen is
already holding that body; a second route returning the same fields would be a
second thing to keep in step with `UserSummary`.

**Sign-out-everywhere is a POST to a named action, not `DELETE /profile/sessions`.**
A collection route promises a collection, and there is none: Redis keys sessions
by token fingerprint, so a user's sessions cannot be listed — which is precisely
why `users.sessions_valid_from` exists.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Request, Response

from nexovue_api.auth.cookies import clear_session_cookie, set_session_cookie
from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.auth.schemas import UserSummary
from nexovue_api.auth.sessions import session_token_from_headers
from nexovue_api.deps import AppSettings, DbSession, SessionStoreDep
from nexovue_api.profile import service
from nexovue_api.profile.schemas import PasswordChange, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["profile"])


@router.patch("", summary="Change your own display name")
async def update_profile(
    payload: ProfileUpdate, identity: CurrentIdentity, db: DbSession
) -> UserSummary:
    """`db` is taken so the request dependency commits: the row `identity` holds
    is attached to that session, so assigning to it is the write."""
    return service.rename(identity.user, payload)


@router.post(
    "/password",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Change your own password, ending every other session",
)
async def change_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    identity: CurrentIdentity,
    db: DbSession,
    sessions: SessionStoreDep,
    settings: AppSettings,
) -> None:
    """Requires the current password, and ends every session except this one.

    **This session is kept, and replaced.** The caller proved they hold the
    current password, which is the one thing a stolen session cannot do, so
    signing them out of the device in front of them protects nobody. The
    response carries a freshly minted session cookie; every other session the
    account had is refused from here on. The reasoning is in `service.py`.

    204 because both facts are constants of this endpoint rather than results of
    the request — the screen can say "your password was changed and you have
    been signed out everywhere else" from having called it.
    """
    token = await service.change_password(
        db,
        sessions,
        user=identity.user,
        current_token=session_token_from_headers(request.headers),
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    set_session_cookie(response, token, settings)


@router.post(
    "/sign-out-everywhere",
    status_code=HTTPStatus.NO_CONTENT,
    summary="End every session on this account, including this one",
)
async def sign_out_everywhere(
    request: Request,
    response: Response,
    identity: CurrentIdentity,
    db: DbSession,
    sessions: SessionStoreDep,
    settings: AppSettings,
) -> None:
    """Ends this session too, so the cookie is cleared and the screen sends the
    customer to sign in — which is also how they learn it worked."""
    await service.sign_out_everywhere(
        db,
        sessions,
        user=identity.user,
        current_token=session_token_from_headers(request.headers),
    )
    clear_session_cookie(response, settings)
