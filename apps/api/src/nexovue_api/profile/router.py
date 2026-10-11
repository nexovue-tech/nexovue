"""The account of whoever is signed in. HTTP only.

No route takes a user id. `CurrentIdentity` names the account, so there is no
value in a path or a body that could point these anywhere else.

**No `GET` on the account itself.** `/auth/me` already answers "who is signed
in" and the screen is already holding that body; a second route returning the
same fields would be a second thing to keep in step with `UserSummary`. The two
`GET`s below answer questions `/auth/me` does not: what is signed in, and
whether a code is needed to sign in.

**Sign-out-everywhere stays a POST to a named action** rather than becoming
`DELETE /profile/sessions`, even though the collection now exists. The two do
different things and only one of them is a collection operation: ending a single
session deletes its key, while ending them all moves
`users.sessions_valid_from` — an epoch that also refuses sessions nothing has
enumerated. A `DELETE` on the collection would promise the first mechanism and
perform the second.

**`DELETE /profile/sessions/{session_id}` takes an id and is still not a route
that can name somebody else's session.** The id is proved to be in the caller's
own index before anything is deleted, and an id that is not is answered with the
same 404 as one that never existed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Request, Response

from nexovue_api.audit import service as audit
from nexovue_api.auth import second_factor
from nexovue_api.auth.cookies import clear_session_cookie, set_session_cookie
from nexovue_api.auth.dependencies import (
    CredentialEncryption,
    CurrentIdentity,
    OptionalCredentialEncryption,
    SecondFactors,
)
from nexovue_api.auth.schemas import UserSummary
from nexovue_api.auth.sessions import session_token_from_headers, token_fingerprint
from nexovue_api.deps import (
    AppSettings,
    DbSession,
    EmailDelivery,
    OneTimeTokens,
    SessionStoreDep,
)
from nexovue_api.errors.exceptions import NotFoundError, PermissionDeniedError
from nexovue_api.profile import personal_data, service
from nexovue_api.profile.schemas import (
    AccountDeletion,
    AccountDeletionState,
    EmailChangeRequest,
    PasswordChange,
    ProfileUpdate,
    RecoveryCodes,
    SecondFactorConfirmation,
    SecondFactorEnrolment,
    SecondFactorEnrolmentRequest,
    SecondFactorRemoval,
    SecondFactorState,
    SessionSummary,
)

# One refusal for a session id that is not the caller's and one that never
# existed. See `end_session`.
NO_SUCH_SESSION: Final = "There is no such session on this account."
# The person's data as a file: addressed by its extension (`api.md`), and declared
# above `/sessions/{session_id}` like every file route.
PERSONAL_DATA_FILE_PATH: Final = "/data.json"
# A support view reads as the person, but their data file is theirs to take.
EXPORT_NOT_IN_SUPPORT_VIEW: Final = "Only the person can download their own data."

router = APIRouter(prefix="/profile", tags=["profile"])


@router.patch("", summary="Change your own display name and time zone")
async def update_profile(
    payload: ProfileUpdate, identity: CurrentIdentity, db: DbSession
) -> UserSummary:
    """`db` is taken so the request dependency commits: the row `identity` holds
    is attached to that session, so assigning to it is the write."""
    return service.update_details(identity.user, payload)


@router.post(
    "/email",
    status_code=HTTPStatus.ACCEPTED,
    summary="Ask to move this account to a different email address",
)
async def request_email_change(
    payload: EmailChangeRequest,
    identity: CurrentIdentity,
    db: DbSession,
    tokens: OneTimeTokens,
    email: EmailDelivery,
    settings: AppSettings,
) -> None:
    """Mails a confirmation link to the **new** address and changes nothing yet.

    202 rather than 204: a message has been handed to a provider, not delivered,
    and the status should not claim more than that — the same reading
    `/auth/verify-email/resend` takes.

    The address moves when the link is followed, at
    `POST /auth/email-change/confirm`. That endpoint is unauthenticated and
    lives in `auth` because it is opened from a mail client, which is a
    different browser session from this one and often a different device.

    The link's lifetime is `verify_email_ttl_seconds` and not a setting of its
    own: this *is* an address verification, pointed at an address the account
    does not hold yet, and somebody switching inboxes needs the same unhurried
    day a new signup gets.
    """
    await service.request_email_change(
        db,
        tokens,
        email,
        user=identity.user,
        current_password=payload.current_password,
        new_address=payload.new_email,
        app_base_url=settings.app_base_url,
        ttl_seconds=settings.verify_email_ttl_seconds,
    )


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


@router.get("/sessions", summary="What is signed in as you")
async def list_sessions(
    request: Request,
    identity: CurrentIdentity,
    sessions: SessionStoreDep,
) -> list[SessionSummary]:
    """The list beside sign-out-everywhere, which is the thing a worried customer
    actually wants: not "end everything" but "is one of these not me?".

    Newest first, the caller's own marked. No id is taken and none could be: the
    user whose sessions these are comes from the session this request arrived on.
    """
    return await service.list_sessions(
        sessions,
        user=identity.user,
        current_token=session_token_from_headers(request.headers),
    )


@router.get(
    PERSONAL_DATA_FILE_PATH,
    response_class=Response,
    summary="Download what nexovue holds about you",
)
async def export_personal_data(identity: CurrentIdentity, db: DbSession) -> Response:
    """One JSON file, saved rather than shown. Audited: it holds where you signed in from.

    `CurrentIdentity` already refuses an API token; a support view is refused here,
    because reading as the person is not the person taking their file.
    """
    if identity.impersonation is not None:
        raise PermissionDeniedError(EXPORT_NOT_IN_SUPPORT_VIEW)
    exported = await personal_data.export(
        db, user=identity.user, organization=identity.organization, now=datetime.now(UTC)
    )
    audit.record_personal_data_exported(db, user=identity.user)
    return Response(
        content=exported.model_dump_json(indent=2),
        media_type="application/json",
        headers={"content-disposition": 'attachment; filename="nexovue-your-data.json"'},
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=HTTPStatus.NO_CONTENT,
    summary="End one session on this account",
)
async def end_session(
    session_id: str,
    request: Request,
    response: Response,
    identity: CurrentIdentity,
    db: DbSession,
    sessions: SessionStoreDep,
    settings: AppSettings,
) -> None:
    """End a single session, which may be this one.

    **404 for an id that is not the caller's**, whether it belongs to another
    account or to nothing at all. `NotFoundError` is already documented as
    deliberately indistinguishable from a resource in another organization, and
    the same reasoning applies one level down: a caller must not learn that
    somebody else's session is live by the shape of the refusal.

    Ending your own current session clears the cookie, because leaving the
    browser holding a token the store has just deleted would turn the customer's
    deliberate act into a mystery 401 on their next click.
    """
    if not await service.end_one_session(db, sessions, user=identity.user, session_id=session_id):
        raise NotFoundError(NO_SUCH_SESSION)
    if session_id == _current_session_id(request):
        clear_session_cookie(response, settings)


def _current_session_id(request: Request) -> str | None:
    """The id the caller's own session appears under in their list."""
    token = session_token_from_headers(request.headers)
    return token_fingerprint(token) if token else None


@router.get("/two-factor", summary="Whether a code is needed to sign in as you")
async def second_factor_state(identity: CurrentIdentity, db: DbSession) -> SecondFactorState:
    """The state, never the secret. There is no route that returns the secret
    after enrolment, because there is nothing a customer could do with it that
    re-enrolling would not do more safely."""
    status = await second_factor.describe(db, user=identity.user)
    return SecondFactorState(
        enabled=status.enabled,
        confirmed_at=status.confirmed_at,
        recovery_codes_remaining=status.recovery_codes_remaining,
    )


@router.post("/two-factor/enrol", summary="Offer a secret to an authenticator app")
async def begin_second_factor_enrolment(
    payload: SecondFactorEnrolmentRequest,
    identity: CurrentIdentity,
    second_factors: SecondFactors,
    box: CredentialEncryption,
) -> SecondFactorEnrolment:
    """**Nothing on the account changes here.** This hands back a secret and
    holds a sealed copy for ten minutes; the factor is armed by `confirm`, and
    an offer nobody confirms expires on its own.

    200 rather than 201: no resource was created, and a 201 would invite a client
    to believe the second factor is now on.

    The only response in this API that carries a TOTP secret. It is gated on the
    current password, and a deployment with no encryption key gets 503 from the
    dependency rather than a secret stored in the clear.
    """
    offer = await second_factor.begin_enrolment(
        second_factors,
        box,
        user=identity.user,
        current_password=payload.current_password,
    )
    return SecondFactorEnrolment(secret=offer.secret, provisioning_uri=offer.provisioning_uri)


@router.post("/two-factor/confirm", summary="Prove the code and switch it on")
async def confirm_second_factor(
    payload: SecondFactorConfirmation,
    identity: CurrentIdentity,
    db: DbSession,
    second_factors: SecondFactors,
    box: CredentialEncryption,
) -> RecoveryCodes:
    """The step that arms the factor, and the only place recovery codes are shown.

    **Confirmation exists so nobody locks themselves out.** A secret stored
    without a proved code belongs to an account whose owner may have mistyped it
    into their authenticator, and the next thing that would happen to them is
    being refused by a control they were trying to add.

    No session is ended. The customer is signed in on the device they enrolled
    from, and adding a factor says nothing about the password being in anybody
    else's hands.
    """
    codes = await second_factor.confirm_enrolment(
        db, second_factors, box, user=identity.user, code=payload.code
    )
    return RecoveryCodes(recovery_codes=list(codes))


@router.post(
    "/two-factor/disable",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Turn off two-factor authentication",
)
async def disable_second_factor(
    payload: SecondFactorRemoval,
    identity: CurrentIdentity,
    db: DbSession,
    second_factors: SecondFactors,
) -> None:
    """Gated on the current password, exactly as turning it on is.

    Disarming removes protection its owner deliberately added, so it has to be
    beyond the reach of a session somebody else is holding — which is the same
    caller the arming gate refuses, arriving from the other direction.

    The stored secret and every recovery code go with it. Nothing is kept "in
    case they turn it back on": a secret that outlived its enrolment is a
    credential nobody is watching.
    """
    await second_factor.disable(
        db,
        second_factors,
        user=identity.user,
        current_password=payload.current_password,
    )


# ---- your data (docs/features/personal-data/spec.md) -------------------------


@router.get("/deletion", summary="Whether you may delete your account")
async def account_deletion_state(identity: CurrentIdentity, db: DbSession) -> AccountDeletionState:
    """Read before the button is shown, so the last owner is told before typing a password."""
    refusal = await personal_data.deletion_refusal(db, user=identity.user)
    return AccountDeletionState(allowed=refusal is None, refusal=refusal)


@router.post(
    "/deletion",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Delete your own account",
)
async def delete_account(
    payload: AccountDeletion,
    response: Response,
    identity: CurrentIdentity,
    db: DbSession,
    second_factors: SecondFactors,
    box: OptionalCredentialEncryption,
    settings: AppSettings,
) -> None:
    """A POST to a named action rather than `DELETE /profile`: it carries the
    password, and a body on DELETE has no defined meaning (RFC 9110 §9.3.5).

    Every session ends with the account, this one included — its row is deleted in
    this transaction — so the cookie is cleared.
    """
    await personal_data.delete_account(
        db,
        second_factors,
        box,
        user=identity.user,
        organization=identity.organization,
        current_password=payload.current_password,
        code=payload.code,
        now=datetime.now(UTC),
    )
    clear_session_cookie(response, settings)
