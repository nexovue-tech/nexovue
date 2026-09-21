"""The admin API's shapes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field

from nexovue_api.auth.schemas import EmailAddress
from nexovue_api.usage.schemas import UsageResponse
from nexovue_api.wire import IsoTimestamp


class OrganizationRow(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    region: str
    plan: str
    created_at: IsoTimestamp
    deleted: bool
    suspended: bool


class OrganizationSearch(BaseModel):
    organizations: list[OrganizationRow]
    """Whether the cap cut the list short, so the screen can say so rather than
    letting an operator read a partial list as the whole one."""
    truncated: bool = False


class MemberRow(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    role: str
    # `active` has a password; `invited` does not yet.
    status: str


class ProjectRow(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    # The last day anything was metered for it; null for a project that never sent.
    last_ingest_day: date | None


class Suspension(BaseModel):
    at: IsoTimestamp
    reason: str


class PlanGrantRow(BaseModel):
    id: uuid.UUID
    plan: str
    services: int
    ends_at: IsoTimestamp
    reason: str


class OrganizationPage(BaseModel):
    organization: OrganizationRow
    suspension: Suspension | None
    # The complimentary plan in force, if any (FR27); the paid plan is `organization.plan`.
    plan_grant: PlanGrantRow | None
    members: list[MemberRow]
    projects: list[ProjectRow]
    # This calendar month, from the same read the customer's Usage screen makes.
    usage: UsageResponse | None


class WebsitePaymentIn(BaseModel):
    """The payment the website took (Razorpay), recorded once (ADR-008, step 8)."""

    razorpay_payment_id: Annotated[str, Field(min_length=3, max_length=64)]
    amount_minor: Annotated[int, Field(gt=0)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class OnboardRequest(BaseModel):
    organization_name: Annotated[str, Field(min_length=1, max_length=200)]
    owner_email: EmailAddress
    owner_name: Annotated[str | None, Field(max_length=200)] = None
    region: str = "india"
    payment: WebsitePaymentIn


class Onboarded(BaseModel):
    organization_id: uuid.UUID
    # False when the welcome email did not go: resend it from the organization page.
    owner_link_sent: bool


class SuspendRequest(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class GrantRequest(BaseModel):
    plan: Annotated[str, Field(min_length=1, max_length=64)]
    services: Annotated[int, Field(gt=0, le=10_000)]
    ends_on: date
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class ViewAsRequest(BaseModel):
    user_id: uuid.UUID
    reason: Annotated[str, Field(min_length=3, max_length=500)]


class ViewAsTicketOut(BaseModel):
    """Open this in a new tab: it signs the browser in as the person, read-only."""

    url: str
