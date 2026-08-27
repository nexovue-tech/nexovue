"""What the identity endpoints put on the wire.

`UserSummary` and `OrganizationSummary` are the shared shape of "who is signed
in": login, signup and `/auth/me` all answer with them, so a client learns one
structure rather than three.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, Field

from nexovue_api.data.postgres.models import EMAIL_MAX_LENGTH

# NIST SP 800-63B: length is the control that matters, and a maximum exists only
# so a login attempt cannot ask us to argon2 a megabyte.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
EmailAddress = Annotated[str, Field(min_length=3, max_length=EMAIL_MAX_LENGTH)]


class UserSummary(BaseModel):
    """The signed-in person. `name` is null until an invited user fills it in."""

    id: uuid.UUID
    email: str
    name: str | None
    role: str


class OrganizationSummary(BaseModel):
    """The tenant the session is bound to."""

    id: uuid.UUID
    slug: str
    name: str


class IdentityResponse(BaseModel):
    """The body login and `/auth/me` share."""

    user: UserSummary
    organization: OrganizationSummary


class LoginRequest(BaseModel):
    email: EmailAddress
    password: Password
