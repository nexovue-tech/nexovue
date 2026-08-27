"""Creating an account.

Public and unauthenticated, so the request has already passed the per-IP and
per-email auth tier in `ratelimit/policy.py` before it reaches this module.

Signup establishes a session as well as a tenant: a customer who has just typed
a password should not be asked for it again to see the screen that tells them
their key.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Response

from nexovue_api.auth.cookies import set_session_cookie
from nexovue_api.auth.schemas import OrganizationSummary
from nexovue_api.deps import AppSettings, DbSession, SessionStoreDep
from nexovue_api.projects.schemas import ProjectSummary
from nexovue_api.signup import service
from nexovue_api.signup.schemas import SignupRequest, SignupResponse

router = APIRouter(tags=["signup"])


@router.post(
    "/signup",
    status_code=HTTPStatus.CREATED,
    summary="Create an organization, its owner, its first project and its first key",
)
async def sign_up(
    payload: SignupRequest,
    response: Response,
    db: DbSession,
    sessions: SessionStoreDep,
    settings: AppSettings,
) -> SignupResponse:
    tenant = await service.provision_tenant(
        db,
        organization_name=payload.organization_name,
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )

    token, _ = await sessions.create(
        user_id=str(tenant.user.id), org_id=str(tenant.organization.id)
    )
    set_session_cookie(response, token, settings)

    return SignupResponse(
        organization=OrganizationSummary(
            id=tenant.organization.id,
            slug=tenant.organization.slug,
            name=tenant.organization.name,
        ),
        project=ProjectSummary(
            id=tenant.project.id, slug=tenant.project.slug, name=tenant.project.name
        ),
        api_key=tenant.api_key,
        otlp_endpoint=settings.otlp_endpoint,
    )
