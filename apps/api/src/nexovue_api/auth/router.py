"""Sign in, sign out, and who am I.

HTTP only. The cookie is set and cleared here because it is a transport
concern; the decision about whether the credentials were right belongs to
`service.py` and the token itself belongs to the session store.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Request, Response

from nexovue_api.auth import recovery, second_factor, service
from nexovue_api.auth.cookies import clear_session_cookie, set_session_cookie
from nexovue_api.auth.dependencies import (
    CurrentClient,
    CurrentIdentity,
    OptionalCredentialEncryption,
    SecondFactors,
)
from nexovue_api.auth.schemas import (
    IdentityResponse,
    LoginRequest,
    PasswordReset,
    PasswordResetRequest,
    SecondFactorChallenge,
    SecondFactorSubmission,
    TokenSubmission,
)
from nexovue_api.auth.sessions import session_token_from_headers
from nexovue_api.deps import (
    AppSettings,
    DbSession,
    EmailDelivery,
    OneTimeTokens,
    SessionStoreDep,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", summary="Exchange credentials for a session")
async def login(
    payload: LoginRequest,
    response: Response,
    db: DbSession,
    sessions: SessionStoreDep,
    second_factors: SecondFactors,
    client: CurrentClient,
    settings: AppSettings,
) -> IdentityResponse | SecondFactorChallenge:
    """One of two answers, and the caller tells them apart by a field.

    **No cookie is set on the challenge path.** A caller who has proved the
    password holds one factor of two and must not be able to reach a single
    screen with it — so the session is minted in `submit_second_factor` and
    nowhere else. The challenge is a token in the body rather than a cookie for
    the same reason: it is not a session and should not travel like one.

    The challenge body names nothing about the account. Somebody who has guessed
    a password correctly learns that and no more.
    """
    user, organization = await service.authenticate(
        db, email=payload.email, password=payload.password
    )
    if second_factor.is_enabled(user):
        return SecondFactorChallenge(
            challenge=await second_factors.open_challenge(user_id=str(user.id))
        )

    token = await service.start_session(db, sessions, user=user, client=client)
    set_session_cookie(response, token, settings)
    return service.describe_identity(user, organization)


@router.post("/login/second-factor", summary="Finish a sign-in with a code")
async def submit_second_factor(
    payload: SecondFactorSubmission,
    response: Response,
    db: DbSession,
    sessions: SessionStoreDep,
    second_factors: SecondFactors,
    box: OptionalCredentialEncryption,
    client: CurrentClient,
    settings: AppSettings,
) -> IdentityResponse:
    """Spend a challenge and, if the code is right, mint the session.

    Unauthenticated, like `login`: it *is* the sign-in. What stands in for a
    session is the challenge, which was issued only after the password was
    proved, expires in minutes and is worth five attempts.

    **Every failure is one 401 with one message** — a challenge that never
    existed, one that expired, one whose attempts are spent, and a wrong code
    alike. Distinguishing them would tell somebody holding a stolen password
    which part of their attack is working.

    The box is optional so a deployment with no encryption key still admits a
    recovery code; see `auth/dependencies.get_optional_credential_box`.
    """
    user, organization = await service.authenticate_second_factor(
        db,
        second_factors,
        box,
        challenge_token=payload.challenge,
        code=payload.code,
    )
    token = await service.start_session(db, sessions, user=user, client=client)
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


@router.post(
    "/verify-email/resend",
    status_code=HTTPStatus.ACCEPTED,
    summary="Send a fresh verification link to your own address",
)
async def resend_verification(
    identity: CurrentIdentity,
    tokens: OneTimeTokens,
    email: EmailDelivery,
    settings: AppSettings,
) -> None:
    """Authenticated, and it mails `identity.user.email` rather than any address
    in the body: a caller-chosen recipient would let anybody send mail from our
    verified domain to wherever they liked.

    202 rather than 204 — the mail has been handed to a provider, not delivered,
    and the status should not claim more than that. An address that is already
    verified is also a 202 and sends nothing, because scolding somebody whose
    account is already fine helps nobody.
    """
    await recovery.send_verification(
        tokens,
        email,
        user=identity.user,
        app_base_url=settings.app_base_url,
        ttl_seconds=settings.verify_email_ttl_seconds,
    )


@router.post(
    "/verify-email",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Prove control of the address on the account",
)
async def verify_email(payload: TokenSubmission, db: DbSession, tokens: OneTimeTokens) -> None:
    """Unauthenticated on purpose: the customer clicks this from a mail client,
    which is a different browser session from the one they signed up in — often
    a different device.

    Writes `users.email_verified_at`, which has existed unwritten since
    migration 0001. It unlocks nothing in Phase 1; see `recovery.verify_email`.
    """
    await recovery.verify_email(db, tokens, token=payload.token)


@router.post(
    "/email-change/confirm",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Move the account to the address a change link was sent to",
)
async def confirm_email_change(
    payload: TokenSubmission, db: DbSession, tokens: OneTimeTokens
) -> None:
    """Unauthenticated for the same reason `verify-email` is: the link is opened
    from a mail client at the *new* address, which is a different browser
    session from the one that asked and often a different device.

    The address it moves the account to comes from the token, not from the body
    and not from a column — so this endpoint takes no address and cannot be
    pointed at one. Asking for the change is `POST /profile/email`, which is
    where the current password is proved.

    **No session is issued and none is ended.** Nothing here suggests the
    password is in anybody else's hands, and the customer is already signed in
    on the device they asked from.
    """
    await recovery.confirm_email_change(db, tokens, token=payload.token)


@router.post(
    "/password-reset",
    status_code=HTTPStatus.ACCEPTED,
    summary="Ask for a password reset link",
)
async def request_password_reset(
    payload: PasswordResetRequest,
    db: DbSession,
    tokens: OneTimeTokens,
    email: EmailDelivery,
    settings: AppSettings,
) -> None:
    """Always 202, for a known and an unknown address alike.

    The status is the whole security property of this endpoint: anything that
    varies with whether the address has an account — the code, the body, a
    delay — turns the form into an account-existence oracle.
    """
    await recovery.request_password_reset(
        db,
        tokens,
        email,
        address=payload.email,
        app_base_url=settings.app_base_url,
        ttl_seconds=settings.reset_password_ttl_seconds,
    )


@router.post(
    "/password-reset/confirm",
    status_code=HTTPStatus.NO_CONTENT,
    summary="Set a new password with a reset link's token",
)
async def confirm_password_reset(
    payload: PasswordReset,
    response: Response,
    db: DbSession,
    tokens: OneTimeTokens,
    settings: AppSettings,
) -> None:
    """Sets the password and ends every live session, including the caller's.

    **No session is issued back.** Signing the customer in here would be
    convenient and wrong: the person holding the link proved they control the
    address, and the next step is to sign in with the password they just chose
    — which is also the step that tells them it worked. The cookie is cleared
    so a browser that was signed in as somebody does not keep a session the
    reset just invalidated.
    """
    await recovery.reset_password(db, tokens, token=payload.token, new_password=payload.password)
    clear_session_cookie(response, settings)
