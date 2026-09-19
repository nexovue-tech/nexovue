"""The admin API (F31). HTTP only; the rules are in `service.py`.

Every route but sign-in requires a staff session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from nexovue_api.admin import service
from nexovue_api.admin.dependencies import CurrentStaff, StaffSessions
from nexovue_api.admin.schemas import (
    GrantRequest,
    Onboarded,
    OnboardRequest,
    OrganizationPage,
    OrganizationSearch,
    StaffSignIn,
    StaffSummary,
    SuspendRequest,
    ViewAsRequest,
    ViewAsTicketOut,
)
from nexovue_api.auth.encryption import STAFF_TOTP_PURPOSE, credential_box
from nexovue_api.auth.totp import SecondFactorStore
from nexovue_api.deps import AppSettings, DbSession, OptionalEmail, RedisClient
from nexovue_api.staff import service as staff
from nexovue_api.staff.sessions import STAFF_COOKIE_NAME, STAFF_SESSION_TTL_SECONDS
from nexovue_api.support import service as support
from nexovue_api.support.tickets import TicketStore

router = APIRouter(tags=["admin"])


@router.post("/sign-in", summary="Sign a staff member in: address, password and code")
async def sign_in(
    payload: StaffSignIn,
    response: Response,
    db: DbSession,
    redis: RedisClient,
    sessions: StaffSessions,
    settings: AppSettings,
) -> StaffSummary:
    box = credential_box(settings.credential_encryption_key, purpose=STAFF_TOTP_PURPOSE)
    account = await staff.sign_in(
        db,
        box,
        SecondFactorStore(redis),
        email=payload.email,
        password=payload.password,
        code=payload.code,
    )
    token = await sessions.create(staff_id=account.id)
    response.set_cookie(
        key=STAFF_COOKIE_NAME,
        value=token,
        max_age=STAFF_SESSION_TTL_SECONDS,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        # Strict: nothing on another site has any reason to reach this console.
        samesite="strict",
    )
    return StaffSummary(id=account.id, email=account.email, name=account.name)


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT, summary="End this staff session")
async def sign_out(staff_member: CurrentStaff, response: Response) -> None:
    response.delete_cookie(key=STAFF_COOKIE_NAME, path="/")


@router.get("/me", summary="Who is signed in to the admin console")
async def me(staff_member: CurrentStaff) -> StaffSummary:
    return StaffSummary(id=staff_member.id, email=staff_member.email, name=staff_member.name)


@router.get("/organizations", summary="Find an organization by name, slug, id or member email")
async def search_organizations(
    staff_member: CurrentStaff, db: DbSession, q: str
) -> OrganizationSearch:
    return await service.search(db, q)


@router.post(
    "/organizations",
    status_code=status.HTTP_201_CREATED,
    summary="Onboard a paying customer: organization, owner and website payment",
)
async def onboard(
    staff_member: CurrentStaff,
    db: DbSession,
    request: OnboardRequest,
    email: OptionalEmail,
    settings: AppSettings,
) -> Onboarded:
    owner, organization, token = await service.onboard(
        db, staff=staff_member, request=request, now=datetime.now(UTC)
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
    staff_member: CurrentStaff,
    db: DbSession,
    org_id: uuid.UUID,
    email: OptionalEmail,
    settings: AppSettings,
) -> Onboarded:
    owner, organization, token = await service.new_owner_link(
        db, staff=staff_member, org_id=org_id, now=datetime.now(UTC)
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
    staff_member: CurrentStaff, db: DbSession, org_id: uuid.UUID, request: SuspendRequest
) -> None:
    await service.suspend(
        db, staff=staff_member, org_id=org_id, reason=request.reason, now=datetime.now(UTC)
    )


@router.delete(
    "/organizations/{org_id}/suspension",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Resume this organization's ingest",
)
async def resume(staff_member: CurrentStaff, db: DbSession, org_id: uuid.UUID) -> None:
    await service.resume(db, staff=staff_member, org_id=org_id)


@router.post(
    "/organizations/{org_id}/plan-grant",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Give a complimentary plan until a date",
)
async def grant_plan(
    staff_member: CurrentStaff, db: DbSession, org_id: uuid.UUID, request: GrantRequest
) -> None:
    await service.grant_plan(
        db, staff=staff_member, org_id=org_id, request=request, now=datetime.now(UTC)
    )


@router.delete(
    "/organizations/{org_id}/plan-grant",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the complimentary plan now",
)
async def end_grant(staff_member: CurrentStaff, db: DbSession, org_id: uuid.UUID) -> None:
    await service.end_grant(db, staff=staff_member, org_id=org_id, now=datetime.now(UTC))


@router.post(
    "/organizations/{org_id}/view-as",
    summary="View the console as one of this organization's people, read-only",
)
async def view_as(
    staff_member: CurrentStaff,
    db: DbSession,
    redis: RedisClient,
    settings: AppSettings,
    org_id: uuid.UUID,
    request: ViewAsRequest,
) -> ViewAsTicketOut:
    """Only inside the owner's grant (BR14); the ticket works once, for 60 seconds,
    on the customer host — never the staff cookie there (ADR-016)."""
    ticket = await support.start_view(
        db,
        TicketStore(redis),
        org_id=org_id,
        user_id=request.user_id,
        staff_label=service.staff_label(staff_member),
        reason=request.reason,
        now=datetime.now(UTC),
    )
    base = settings.app_base_url.rstrip("/")
    return ViewAsTicketOut(url=f"{base}/api/v1/support/view-as?ticket={ticket}")


@router.get("/organizations/{org_id}", summary="One organization, as staff see it")
async def organization(
    staff_member: CurrentStaff, db: DbSession, org_id: uuid.UUID
) -> OrganizationPage:
    return await service.page(db, org_id=org_id)
