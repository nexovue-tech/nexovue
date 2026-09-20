"""What the admin console does (F31). No HTTP here."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

import structlog
from nexovue_platform.email import EmailError, EmailSender, Message
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.admin import queries
from nexovue_api.admin.schemas import (
    GrantRequest,
    MemberRow,
    OnboardRequest,
    OrganizationPage,
    OrganizationRow,
    OrganizationSearch,
    PlanGrantRow,
    ProjectRow,
    Suspension,
)
from nexovue_api.audit import service as audit
from nexovue_api.auth import set_password
from nexovue_api.data.postgres.models import (
    Organization,
    PlanGrant,
    User,
    WebsitePayment,
)
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.organizations import valve
from nexovue_api.signup.service import provision_tenant
from nexovue_api.usage import queries as usage_queries
from nexovue_api.usage.service import usage_for_organization

MIN_NEEDLE: Final = 2
NO_SUCH_ORGANIZATION: Final = "No such organization."
# The same seven days an invite gets (D5).
OWNER_LINK_LIFETIME: Final = timedelta(days=7)
PAYMENT_ALREADY_RECORDED: Final = "That payment is already recorded against an organization."
UNKNOWN_PLAN: Final = "No such plan."
GRANT_IN_THE_PAST: Final = "A grant must end after today."
NO_GRANT: Final = "This organization has no complimentary plan in force."
OWNER_ALREADY_ACTIVE: Final = "The owner has already set a password; there is no link to resend."

logger = structlog.get_logger(__name__)


def operator_label(operator: User) -> str:
    """How nexovue is named in a customer's audit log when we act (FR37).

    A label, not a link: a customer reads their own history and should see who
    it was, not the id of a row in a table they cannot open.
    """
    return f"{operator.name or operator.email} at nexovue"


async def onboard(
    db: AsyncSession, *, operator: User, request: OnboardRequest, now: datetime
) -> tuple[User, Organization, str]:
    """A paying customer's organization, owner and payment, in one transaction (FR22).

    `provision_tenant` makes exactly the rows signup makes (AC24); the owner has no
    password, and the link returned sets one.
    """
    # Checked before anything is made: a payment buys one organization. The unique
    # index is the guarantee under a race; this is the clear answer.
    if await queries.payment_is_recorded(db, request.payment.razorpay_payment_id):
        raise ConflictError(PAYMENT_ALREADY_RECORDED)
    tenant = await provision_tenant(
        db,
        organization_name=request.organization_name.strip(),
        email=request.owner_email,
        password=None,
        name=request.owner_name,
        region=request.region,
    )
    token = set_password.issue(tenant.user, now=now, lifetime=OWNER_LINK_LIFETIME)
    db.add(
        WebsitePayment(
            org_id=tenant.organization.id,
            razorpay_payment_id=request.payment.razorpay_payment_id,
            amount_minor=request.payment.amount_minor,
            currency=request.payment.currency,
            recorded_by=operator.id,
        )
    )
    try:
        await db.flush()
    except IntegrityError as recorded:
        raise ConflictError(PAYMENT_ALREADY_RECORDED) from recorded
    label = operator_label(operator)
    audit.record_organization_onboarded(
        db, organization=tenant.organization, owner_email=tenant.user.email, operator_label=label
    )
    audit.record_payment_recorded(
        db,
        organization=tenant.organization,
        payment_id=request.payment.razorpay_payment_id,
        amount_minor=request.payment.amount_minor,
        currency=request.payment.currency,
        operator_label=label,
    )
    return tenant.user, tenant.organization, token


async def new_owner_link(
    db: AsyncSession, *, operator: User, org_id: uuid.UUID, now: datetime
) -> tuple[User, Organization, str]:
    """A fresh link for an owner who has not set a password yet; the old one dies."""
    organization = await db.get(Organization, org_id)
    if organization is None:
        raise NotFoundError(NO_SUCH_ORGANIZATION)
    owner = await queries.pending_owner(db, org_id=org_id)
    if owner is None:
        raise ConflictError(OWNER_ALREADY_ACTIVE)
    token = set_password.issue(owner, now=now, lifetime=OWNER_LINK_LIFETIME)
    audit.record_owner_link_sent(db, owner=owner, operator_label=operator_label(operator))
    return owner, organization, token


async def mail_owner_link(
    email: EmailSender | None,
    *,
    owner: User,
    organization: Organization,
    token: str,
    app_base_url: str,
) -> bool:
    """Never fails the request: an unsent link is resent from the organization page."""
    if email is None:
        logger.warning("owner link created without mail: no email provider configured")
        return False
    try:
        await email.send(
            to=owner.email,
            message=Message.WELCOME_OWNER,
            data={
                "organization": organization.name,
                "link": f"{app_base_url.rstrip('/')}/invite?token={token}",
                "expires_in": "7 days",
            },
            idempotency_key=f"welcome:{set_password.hash_link(token)}",
        )
    except EmailError as failure:
        logger.error("owner link mail refused; the operator can resend", error=str(failure))
        return False
    return True


async def _organization(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    organization = await db.get(Organization, org_id)
    if organization is None:
        raise NotFoundError(NO_SUCH_ORGANIZATION)
    return organization


async def suspend(
    db: AsyncSession, *, operator: User, org_id: uuid.UUID, reason: str, now: datetime
) -> None:
    """The same valve the terminal command closes (FR26, AC28)."""
    organization = await _organization(db, org_id)
    await valve.suspend(
        db, organization, reason=reason, actor_label=operator_label(operator), now=now
    )


async def resume(db: AsyncSession, *, operator: User, org_id: uuid.UUID) -> None:
    organization = await _organization(db, org_id)
    await valve.resume(db, organization, actor_label=operator_label(operator))


async def grant_plan(
    db: AsyncSession,
    *,
    operator: User,
    org_id: uuid.UUID,
    request: GrantRequest,
    now: datetime,
) -> None:
    """A complimentary plan until a date (FR27). One in force at a time: a new grant
    ends the one it replaces, so there is never a question which applies."""
    organization = await _organization(db, org_id)
    if await usage_queries.find_plan(db, slug=request.plan) is None:
        raise InvalidRequestError(UNKNOWN_PLAN)
    ends_at = datetime.combine(request.ends_on, time.min, tzinfo=UTC)
    if ends_at <= now:
        raise InvalidRequestError(GRANT_IN_THE_PAST)
    current = await usage_queries.active_grant(db, org_id=org_id, at=now)
    if current is not None:
        current.ended_at = now
    db.add(
        PlanGrant(
            org_id=org_id,
            plan_slug=request.plan,
            services=request.services,
            starts_at=now,
            ends_at=ends_at,
            reason=request.reason.strip(),
            granted_by=operator.id,
        )
    )
    audit.record_plan_granted(
        db,
        organization=organization,
        plan_slug=request.plan,
        services=request.services,
        ends_at=ends_at.isoformat(),
        reason=request.reason.strip(),
        actor_label=operator_label(operator),
    )
    await db.flush()


async def end_grant(db: AsyncSession, *, operator: User, org_id: uuid.UUID, now: datetime) -> None:
    organization = await _organization(db, org_id)
    current = await usage_queries.active_grant(db, org_id=org_id, at=now)
    if current is None:
        raise ConflictError(NO_GRANT)
    current.ended_at = now
    audit.record_plan_grant_ended(
        db,
        organization=organization,
        plan_slug=current.plan_slug,
        actor_label=operator_label(operator),
    )
    await db.flush()


async def search(db: AsyncSession, needle: str) -> OrganizationSearch:
    if len(needle.strip()) < MIN_NEEDLE:
        raise InvalidRequestError("Search for at least two characters.")
    found = await queries.search_organizations(db, needle)
    return OrganizationSearch(organizations=[_row(organization) for organization in found])


async def page(db: AsyncSession, *, org_id: uuid.UUID) -> OrganizationPage:
    organization = await db.get(Organization, org_id)
    if organization is None:
        raise NotFoundError(NO_SUCH_ORGANIZATION)
    members = await queries.members_of(db, org_id=org_id)
    projects = await queries.projects_of(db, org_id=org_id)
    last_ingest = await queries.last_ingest_days(db, org_id=org_id)
    today = datetime.now(UTC).date()
    usage = (
        None
        if organization.deleted_at is not None
        else await usage_for_organization(
            db, organization=organization, from_day=date(today.year, today.month, 1), to_day=today
        )
    )
    grant = await usage_queries.active_grant(db, org_id=org_id, at=datetime.now(UTC))
    return OrganizationPage(
        organization=_row(organization),
        plan_grant=PlanGrantRow(
            id=grant.id,
            plan=grant.plan_slug,
            services=grant.services,
            ends_at=grant.ends_at,
            reason=grant.reason,
        )
        if grant is not None
        else None,
        suspension=Suspension(
            at=organization.ingest_suspended_at, reason=organization.ingest_suspended_reason or ""
        )
        if organization.ingest_suspended_at is not None
        else None,
        members=[
            MemberRow(
                id=member.id,
                email=member.email,
                name=member.name,
                role=member.role,
                status="active" if member.password_hash is not None else "invited",
            )
            for member in members
        ],
        projects=[
            ProjectRow(
                id=project.id,
                slug=project.slug,
                name=project.name,
                last_ingest_day=last_ingest.get(project.id),
            )
            for project in projects
        ],
        usage=usage,
    )


def _row(organization: Organization) -> OrganizationRow:
    return OrganizationRow(
        id=organization.id,
        slug=organization.slug,
        name=organization.name,
        region=organization.region,
        plan=organization.plan,
        created_at=organization.created_at,
        deleted=organization.deleted_at is not None,
        suspended=organization.ingest_suspended_at is not None,
    )
