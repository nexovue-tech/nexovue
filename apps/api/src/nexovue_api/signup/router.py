"""Creating an account.

Public and unauthenticated, so the request has already passed the per-IP and
per-email auth tier in `ratelimit/policy.py` before it reaches this module.

Signup establishes a session as well as a tenant: a customer who has just typed
a password should not be asked for it again to see the screen that tells them
their key.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated, Final

import structlog
from fastapi import APIRouter, Depends, Response
from nexovue_platform.email import EmailError, EmailSender
from pydantic import BaseModel

from nexovue_api.auth import recovery
from nexovue_api.auth.cookies import set_session_cookie
from nexovue_api.auth.dependencies import CurrentClient
from nexovue_api.auth.schemas import OrganizationSummary
from nexovue_api.auth.service import start_session
from nexovue_api.auth.tokens import OneTimeTokenStore
from nexovue_api.data.postgres.models import User
from nexovue_api.deps import AppSettings, DbSession, OneTimeTokens, OptionalEmail, SessionStoreDep
from nexovue_api.errors.exceptions import PermissionDeniedError
from nexovue_api.projects.schemas import ProjectSummary
from nexovue_api.settings import Settings
from nexovue_api.signup import service
from nexovue_api.signup.schemas import SignupRequest, SignupResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["signup"])

SIGNUP_CLOSED: Final = (
    "nexovue cloud is set up after purchase — choose a plan at nexovue.com and we will "
    "set up your organization."
)


def require_signup_open(settings: AppSettings) -> None:
    """nexovue's cloud: a customer pays on the website and is onboarded by staff
    (ADR-008, D10). Declared first on the route, so a closed signup is refused
    before any other dependency opens a store."""
    if not settings.signup_enabled:
        raise PermissionDeniedError(SIGNUP_CLOSED)


SignupOpen = Annotated[None, Depends(require_signup_open)]


class SignupStatus(BaseModel):
    """Whether this deployment lets anyone create an organization (F31, FR30)."""

    enabled: bool


@router.get(
    # Not under /signup: that prefix is the strict sign-in rate tier, and the
    # signup screen asks this on every load.
    "/system/signup",
    summary="Whether signup is open on this deployment",
)
async def signup_status(settings: AppSettings) -> SignupStatus:
    return SignupStatus(enabled=settings.signup_enabled)


@router.post(
    "/signup",
    status_code=HTTPStatus.CREATED,
    summary="Create an organization, its owner, its first project and its first key",
)
async def sign_up(
    _: SignupOpen,
    payload: SignupRequest,
    response: Response,
    db: DbSession,
    sessions: SessionStoreDep,
    settings: AppSettings,
    tokens: OneTimeTokens,
    email: OptionalEmail,
    client: CurrentClient,
) -> SignupResponse:
    tenant = await service.provision_tenant(
        db,
        organization_name=payload.organization_name,
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )

    # The session lands in Redis at once, but these rows commit only when the
    # request ends: mint it after the tenant is durable, or a fast next call
    # finds a session naming a tenant Postgres cannot see yet.
    await db.commit()

    # Named, so the very first session is not the one device a customer cannot
    # recognise in their own session list. `client` is optional on `start_session`
    # so signup kept working while the list was being built; it is not optional
    # to a reader deciding whether to end a session.
    token = await start_session(db, sessions, user=tenant.user, client=client)
    set_session_cookie(response, token, settings)
    await _mail_verification(tenant.user, tokens, email, settings)

    return SignupResponse(
        organization=OrganizationSummary(
            id=tenant.organization.id,
            slug=tenant.organization.slug,
            name=tenant.organization.name,
        ),
        project=ProjectSummary(
            id=tenant.project.id,
            slug=tenant.project.slug,
            name=tenant.project.name,
            # Nothing can have arrived for a project created a moment ago, and
            # this is the one place that fact is known without asking.
            first_reported_on=None,
        ),
        api_key=tenant.api_key,
        otlp_endpoint=settings.otlp_endpoint,
    )


async def _mail_verification(
    user: User,
    tokens: OneTimeTokenStore,
    # The plain type, not the `OptionalEmail` alias: that one carries a
    # `Depends` marker and belongs on a route signature, not on a helper the
    # route calls.
    email: EmailSender | None,
    settings: Settings,
) -> None:
    """Send the verification link, and never fail signup because it could not go.

    **This is the one place mail is best-effort, and the reason is the plan's
    own:** verifying *after* signup rather than blocking it *"keeps the
    first-span celebration intact, which is the one moment onboarding earns"*.
    An account that exists with an unverified address is recoverable — the
    customer clicks resend. An account that was never created because a mail
    provider was slow is not.

    It is not silent, which is the other half. The plan also says *"a
    verification link that silently never arrives is worse than a visible
    error"*, so the failure is logged at ERROR with the address's account, and
    the response already carries what the screen needs to offer a resend:
    `email_verified_at` is unset.

    It takes `OptionalEmail` rather than `EmailDelivery` for that reason:
    `EmailDelivery` refuses the whole request when mail is unconfigured, which is
    correct for the two endpoints that exist to send and wrong for signup.
    """
    if email is None:
        logger.warning(
            "signup completed without a verification mail: no email provider configured",
            user_id=str(user.id),
        )
        return
    try:
        await recovery.send_verification(
            tokens,
            email,
            user=user,
            app_base_url=settings.app_base_url,
            ttl_seconds=settings.verify_email_ttl_seconds,
        )
    except EmailError as failure:
        logger.error(
            "signup completed but the verification mail was refused; the customer must use resend",
            user_id=str(user.id),
            error=str(failure),
        )
