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

from nexovue_api.auth.schemas import PASSWORD_MAX_LENGTH, Password
from nexovue_api.data.postgres.models import NAME_MAX_LENGTH

DisplayName = Annotated[str, Field(max_length=NAME_MAX_LENGTH)]

# The password already on the account, held to a *bound* rather than to the rules
# for choosing one. Its only job is to be compared against a stored hash, and
# refusing a seven-character guess with "too short" instead of "wrong" would both
# answer a question the caller has not earned and reject a password that is
# genuinely on some account. The ceiling stays, so no caller can ask us to argon2
# a megabyte.
CurrentPassword = Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class ProfileUpdate(BaseModel):
    """A new display name for the caller.

    `null`, and an empty or all-whitespace string, all mean the same thing: the
    name goes back to unset, which is the column's own NULL. Storing `""` would
    be a third state meaning what NULL already means.

    Required rather than optional. This body has one field, so a request that
    omits it is a client defect and not a valid "change nothing".
    """

    name: DisplayName | None


class PasswordChange(BaseModel):
    """The password on the account now, and the one to replace it with.

    The current password is required and verified. A signed-in-but-stolen
    session must not be able to lock the real owner out, and knowledge of the
    current password is the only thing that separates the two callers.
    """

    current_password: CurrentPassword
    new_password: Password
