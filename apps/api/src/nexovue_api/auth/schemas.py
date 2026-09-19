"""What the identity endpoints put on the wire.

`UserSummary` and `OrganizationSummary` are the shared shape of "who is signed
in": login, signup and `/auth/me` all answer with them, so a client learns one
structure rather than three.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from nexovue_api.data.postgres.models import EMAIL_MAX_LENGTH

# NIST SP 800-63B: length is the control that matters, and a maximum exists only
# so a login attempt cannot ask us to argon2 a megabyte.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
EmailAddress = Annotated[str, Field(min_length=3, max_length=EMAIL_MAX_LENGTH)]

# A six-digit authenticator code or a hyphenated recovery code, in one field.
# Bounded rather than shaped: refusing a five-digit entry with "that is not six
# digits" would answer a question the caller has not earned, and the service has
# one message for every way a code can fail. The ceiling is generous enough for
# a recovery code typed with its separator and spaces.
SECOND_FACTOR_CODE_MAX_LENGTH = 64
SecondFactorCode = Annotated[str, Field(min_length=1, max_length=SECOND_FACTOR_CODE_MAX_LENGTH)]


class UserSummary(BaseModel):
    """The signed-in person. `name` is null until an invited user fills it in."""

    id: uuid.UUID
    email: str
    name: str | None
    role: str
    # The IANA zone this person reads timestamps in, or null when they have not
    # chosen one and the screen should keep using the browser's. Exposed because
    # the profile screen has no other way to show which zone is set, and every
    # screen that renders a timestamp needs it (P1-72).
    timezone: str | None
    # Null until the address is proven (P1-70). Exposed because the app has no
    # other way to know whether to offer a resend — verification gates nothing,
    # so its absence is invisible without this. The moment, not a boolean: "when"
    # is the question support asks, and a boolean cannot answer it.
    email_verified_at: datetime | None
    # What this person may do (`auth/policy.py`), so the console hides a control
    # the API would refuse without restating the table (F26, FR3).
    permissions: list[str]


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


class PasswordResetRequest(BaseModel):
    """Ask for a reset link.

    The address is all it takes, and the answer is the same whether or not it
    has an account — see `recovery`. Nothing here is optional, because a body
    with no address is a client defect rather than a valid "reset nobody".
    """

    email: EmailAddress


class TokenSubmission(BaseModel):
    """A link's token, handed back by the screen the customer landed on.

    Bounded so a caller cannot ask us to hash a megabyte. The token itself is
    43 URL-safe characters from 32 random bytes; the ceiling is generous rather
    than exact, because a token that is the wrong length is refused by finding
    no key, not by its shape.
    """

    token: Annotated[str, Field(min_length=1, max_length=512)]


class SecondFactorChallenge(BaseModel):
    """What login answers when the password was right and is not enough.

    **No identity is in this body, and that is the point.** The password has been
    proved and the account has not been reached yet, so nothing that describes
    the account — its name, its organization, even its address — is disclosed to
    a caller who may only have got the password right.

    `second_factor_required` is a constant `True` rather than an implied
    discriminator, so a client tells the two login answers apart by a field
    rather than by noticing which fields are missing.
    """

    second_factor_required: Literal[True] = True
    challenge: str


class SecondFactorSubmission(BaseModel):
    """The second half of a sign-in: the challenge, and a code.

    One field for both kinds of code. A customer holding a recovery code knows
    it as "the code"; asking them to say which kind it is first would be asking
    them to understand our schema. The service tries the authenticator's code
    and then the recovery codes.
    """

    challenge: Annotated[str, Field(min_length=1, max_length=512)]
    code: SecondFactorCode


class PasswordReset(TokenSubmission):
    """A reset link's token and the password to set behind it.

    The new password is held to the same rules as one chosen at signup: the
    constraint belongs to the password, not to the door it came through.
    """

    password: Password
