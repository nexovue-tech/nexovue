"""The platform API (F31). HTTP only; the rules are in `service.py`.

Every route is nexovue's operator's, and every one is refused unless the
session was re-verified in the last thirty minutes — `MayAdministerThePlatform`
carries both halves (`docs/changes/super-admin-in-the-console/`). There is no
sign-in here: the operator signs in on the console's own page, like everyone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from nexovue_api.admin import service
from nexovue_api.admin.schemas import (
    GrantRequest,
    Onboarded,
    OnboardRequest,
    OrganizationPage,
    OrganizationSearch,
    SuspendRequest,
    ViewAsRequest,
    ViewAsTicketOut,
)
from nexovue_api.auth.policy import MayAdministerThePlatform
from nexovue_api.deps import AppSettings, DbSession, OptionalEmail, RedisClient
from nexovue_api.support import service as support
from nexovue_api.support.tickets import TicketStore

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/organizations", summary="Every customer, or the ones a needle matches")
async def search_organizations(
    operator: MayAdministerThePlatform, db: DbSession, q: str | None = None
) -> OrganizationSearch:
    """`q` absent lists every customer, newest first. It is optional because the
    screen opens as a list and the search narrows it."""
    return await service.search(db, q)


@router.post(
    "/organizations",
    status_code=status.HTTP_201_CREATED,
    summary="Onboard a paying customer: organization, owner and website payment",
)
async def onboard(
    operator: MayAdministerThePlatform,
    db: DbSession,
    request: OnboardRequest,
    email: OptionalEmail,
    settings: AppSettings,
) -> Onboarded:
    owner, organization, token = await service.onboard(
        db, operator=operator.user, request=request, now=datetime.now(UTC)
    )
    # Committed before the mail goes, so a link never names a rolled-back owner.
    await db.commit()
    sent = await service.mail_owner_link(
        email,
        owner=owner,
        organization=organization,
        token=token,
        app_base_url=settings.app_base_url,
    )
    return Onboarded(organization_id=organization.id, owner_link_sent=sent)


@router.post(
    "/organizations/{org_id}/owner-link",
    summary="Send the owner a new set-password link",
)
async def resend_owner_link(
    operator: MayAdministerThePlatform,
    db: DbSession,
    org_id: uuid.UUID,
    email: OptionalEmail,
    settings: AppSettings,
) -> Onboarded:
    owner, organization, token = await service.new_owner_link(
        db, operator=operator.user, org_id=org_id, now=datetime.now(UTC)
    )
    await db.commit()
    sent = await service.mail_owner_link(
        email,
        owner=owner,
        organization=organization,
        token=token,
        app_base_url=settings.app_base_url,
    )
    return Onboarded(organization_id=organization.id, owner_link_sent=sent)


@router.post(
    "/organizations/{org_id}/suspension",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Suspend this organization's ingest, with a reason",
)
async def suspend(
    operator: MayAdministerThePlatform, db: DbSession, org_id: uuid.UUID, request: SuspendRequest
) -> None:
    await service.suspend(
        db, operator=operator.user, org_id=org_id, reason=request.reason, now=datetime.now(UTC)
    )


@router.delete(
    "/organizations/{org_id}/suspension",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Resume this organization's ingest",
)
async def resume(operator: MayAdministerThePlatform, db: DbSession, org_id: uuid.UUID) -> None:
    await service.resume(db, operator=operator.user, org_id=org_id)


@router.post(
    "/organizations/{org_id}/plan-grant",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Give a complimentary plan until a date",
)
async def grant_plan(
    operator: MayAdministerThePlatform, db: DbSession, org_id: uuid.UUID, request: GrantRequest
) -> None:
    await service.grant_plan(
        db, operator=operator.user, org_id=org_id, request=request, now=datetime.now(UTC)
    )


@router.delete(
    "/organizations/{org_id}/plan-grant",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the complimentary plan now",
)
async def end_grant(operator: MayAdministerThePlatform, db: DbSession, org_id: uuid.UUID) -> None:
    await service.end_grant(db, operator=operator.user, org_id=org_id, now=datetime.now(UTC))


@router.post(
    "/organizations/{org_id}/view-as",
    summary="View the console as one of this organization's people, read-only",
)
async def view_as(
    operator: MayAdministerThePlatform,
    db: DbSession,
    redis: RedisClient,
    settings: AppSettings,
    org_id: uuid.UUID,
    request: ViewAsRequest,
) -> ViewAsTicketOut:
    """Only inside the owner's grant (BR14); the ticket works once, for 60 seconds,
    for the owner's own console, and the session it makes is read-only."""
    ticket = await support.start_view(
        db,
        TicketStore(redis),
        org_id=org_id,
        user_id=request.user_id,
        operator_label=service.operator_label(operator.user),
        reason=request.reason,
        now=datetime.now(UTC),
    )
    base = settings.app_base_url.rstrip("/")
    return ViewAsTicketOut(url=f"{base}/api/v1/support/view-as?ticket={ticket}")


@router.get("/organizations/{org_id}", summary="One organization, as the operator sees it")
async def organization(
    operator: MayAdministerThePlatform, db: DbSession, org_id: uuid.UUID
) -> OrganizationPage:
    return await service.page(db, org_id=org_id)
