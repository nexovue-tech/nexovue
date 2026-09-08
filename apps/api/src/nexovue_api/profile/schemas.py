"""What the profile endpoints accept.

Nothing here names a user. The account being changed is the caller's, taken from
the session, so a body that could name one would be a body that could be forged.

The password rules are imported from `auth.schemas` rather than restated: the
constraint belongs to the password, not to the door it came through — which is
the same reasoning `auth.schemas.PasswordReset` already carries.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from nexovue_api.auth.schemas import PASSWORD_MAX_LENGTH, EmailAddress, Password
from nexovue_api.data.postgres.models import NAME_MAX_LENGTH, TIMEZONE_NAME_LENGTH

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
