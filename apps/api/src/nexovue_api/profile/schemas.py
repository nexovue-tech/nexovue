"""What the profile endpoints accept, and what the two reading ones answer.

Nothing here names a user. The account being read or changed is the caller's,
taken from the session, so a body that could name one would be a body that could
be forged. The one path parameter these routes have — a session id — is proved to
be in the caller's own index before it reaches anything.

The password rules are imported from `auth.schemas` rather than restated: the
constraint belongs to the password, not to the door it came through — which is
the same reasoning `auth.schemas.PasswordReset` already carries.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from nexovue_api.auth.schemas import (
    PASSWORD_MAX_LENGTH,
    EmailAddress,
    Password,
    SecondFactorCode,
)
from nexovue_api.data.postgres.models import NAME_MAX_LENGTH, TIMEZONE_NAME_LENGTH
from nexovue_api.wire import IsoTimestamp

DisplayName = Annotated[str, Field(max_length=NAME_MAX_LENGTH)]

# Bounded here and judged in `timezones.py`. The bound exists so no caller can
# hand `zoneinfo` a megabyte to look for on disk; whether the name is a real
# zone is a question only the tz database can answer.
TimezoneName = Annotated[str, Field(max_length=TIMEZONE_NAME_LENGTH)]

# The password already on the account, held to a *bound* rather than to the rules
# for choosing one. Its only job is to be compared against a stored hash, and
# refusing a seven-character guess with "too short" instead of "wrong" would both
# answer a question the caller has not earned and reject a password that is
# genuinely on some account. The ceiling stays, so no caller can ask us to argon2
# a megabyte.
CurrentPassword = Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class ProfileUpdate(BaseModel):
    """The details screen's form: a display name and a time zone.

    For both fields `null`, an empty string and an all-whitespace string mean
    the same thing — back to unset, which is the column's own NULL. Storing `""`
    would be a third state meaning what NULL already means.

    **Both are required, and that is what makes `null` unambiguous.** This is
    the whole form, submitted whole. If a field could be omitted, an absent
    `timezone` would have to mean "leave it alone" while `null` meant "clear
    it", and the screen and the API would have to agree on a distinction that
    buys nothing — a customer who clears their zone is doing it from the same
    form as one who sets it.
    """

    name: DisplayName | None
    timezone: TimezoneName | None


class PasswordChange(BaseModel):
    """The password on the account now, and the one to replace it with.

    The current password is required and verified. A signed-in-but-stolen
    session must not be able to lock the real owner out, and knowledge of the
    current password is the only thing that separates the two callers.
    """

    current_password: CurrentPassword
    new_password: Password


class EmailChangeRequest(BaseModel):
    """The address to move this account to, and the password that authorizes it.

    **The current password is required, and it is not ceremony.** Whoever holds
    the address on an account can reset its password, so changing the address is
    at least as powerful as changing the password — it is the first move in an
    account takeover, not the last. A stolen session is exactly what this
    refuses, on the same reasoning `PasswordChange` carries, and knowing the
    current password is the one thing such a session cannot do.

    The link that follows is not gated again: control of the new inbox is what
    it proves, and the password was proved when it was asked for.
    """

    current_password: CurrentPassword
    new_email: EmailAddress


class SessionSummary(BaseModel):
    """One live session, described by as little as recognition needs.

    **Every field here is published to an intruder as well as to the owner.** The
    screen exists for somebody who suspects another person is signed in as them;
    if that person is, they are reading this list too. `auth/clients.py` carries
    the full reasoning and does the reducing — what reaches this schema is
    already a browser family and a network, never a raw User-Agent and never a
    precise address.
    """

    # The session's token fingerprint: a SHA-256 digest, which cannot be turned
    # back into the token. Holding one grants nothing — ending a session by id
    # requires it to be in the caller's own index first.
    id: str
    created_at: IsoTimestamp
    # When a request last arrived on this session, to the nearest minute. A
    # coarser clock than the store could keep, on purpose: recording it exactly
    # would mean a write per request to answer a question nobody asks that
    # precisely.
    last_seen_at: IsoTimestamp
    # "Chrome on macOS". Null when the client said nothing recognisable, which
    # the screen renders as an unknown device rather than inventing one.
    client: str | None
    # The caller's network in CIDR form, e.g. "203.0.113.0/24". Never the exact
    # address — see `auth/clients.py`.
    network: str | None
    # Which row not to offer an "end this" button for without saying what it
    # does. Computed from the request's own token, never sent by the client.
    current: bool


class SecondFactorState(BaseModel):
    """Whether this account needs a code to sign in, and how much cover is left."""

    enabled: bool
    # When the customer proved a code from the secret. Null while the factor is
    # off. The moment rather than a boolean, for the reason `email_verified_at`
    # is one: "when" is the question support asks.
    confirmed_at: IsoTimestamp | None
    # How many recovery codes have not been spent. Shown so somebody runs out
    # knowingly rather than at the moment they need one.
    recovery_codes_remaining: int


class SecondFactorEnrolment(BaseModel):
    """The secret, returned exactly once and by this endpoint alone.

    Both fields carry the shared secret — `provisioning_uri` embeds it — so
    nothing else in the API ever answers with either of them again. The account
    is unchanged until a code confirms this offer.
    """

    secret: str
    provisioning_uri: str


class RecoveryCodes(BaseModel):
    """Shown once, at the moment the second factor is armed.

    There is no endpoint that returns these again, because there is nothing to
    return: only argon2 hashes are stored. A customer who loses them turns the
    factor off with their password and enrols afresh.
    """

    recovery_codes: list[str]


class SecondFactorEnrolmentRequest(BaseModel):
    """Ask for a secret. Gated on the current password.

    A stolen session is exactly what this refuses: arming a second factor on
    somebody else's account is a way to lock them out of it, and knowing the
    current password is the one thing such a session cannot do — the same
    reasoning `PasswordChange` carries.
    """

    current_password: CurrentPassword


class SecondFactorConfirmation(BaseModel):
    """The code that proves the offered secret reached an authenticator."""

    code: SecondFactorCode


class SecondFactorRemoval(BaseModel):
    """Turn the factor off. Gated on the current password, like turning it on.

    Both directions are gated for the same reason and not out of symmetry:
    disarming removes the protection its owner deliberately added, so it must be
    beyond the reach of a session somebody else is holding.
    """

    current_password: CurrentPassword
