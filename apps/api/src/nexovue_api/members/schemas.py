"""The members API's request and response shapes."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from nexovue_api.auth.schemas import EmailAddress, Password
from nexovue_api.data.postgres.models import NAME_MAX_LENGTH
from nexovue_api.wire import IsoTimestamp

Role = Literal["owner", "admin", "member", "viewer"]

# `active` has a password; `invited` holds a live link; `expired` holds a dead one.
MemberStatus = Literal["active", "invited", "expired"]


class MemberSummary(BaseModel):
    id: uuid.UUID
    email: str
    # Null until an invited person chooses one.
    name: str | None
    role: Role
    status: MemberStatus
    # Who sent the invite, as a name to show; null for the person who signed up.
    invited_by: str | None
    invited_at: IsoTimestamp | None
    # When an invite was last emailed; null means it did not go, and a resend is
    # the way to send it.
    invite_sent_at: IsoTimestamp | None
    invite_expires_at: IsoTimestamp | None
    added_at: IsoTimestamp
    # From the audit log's `session.created`; null for someone who never has.
    last_signed_in_at: IsoTimestamp | None


class MemberListResponse(BaseModel):
    members: list[MemberSummary]


class InviteRequest(BaseModel):
    email: EmailAddress
    role: Role


class RoleChangeRequest(BaseModel):
    role: Role


class InvitationPreview(BaseModel):
    """What the accept screen shows before anyone types a password."""

    organization: str
    email: str
    role: Role
    invited_by: str
    expires_at: IsoTimestamp


class AcceptInvitation(BaseModel):
    token: Annotated[str, Field(min_length=1, max_length=512)]
    name: Annotated[str, Field(min_length=1, max_length=NAME_MAX_LENGTH)]
    password: Password
