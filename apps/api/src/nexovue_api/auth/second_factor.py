"""Turning a second factor on, off, and proving it at sign-in.

No HTTP and no SQL text, like the rest of this package: the router owns the
status code, `totp.py` owns the Redis state, `recovery_codes.py` owns the codes,
and what is left here is the decision.

**Enrolment is two steps and cannot be one.** A secret stored the moment it is
generated belongs to an account whose owner may have mistyped it into their
authenticator, closed the tab, or scanned a QR code that never rendered — and
the next thing that happens to them is being locked out of their own product by
a control they were trying to add. So the first step only *offers* a secret, the
offer lives in Redis and expires on its own, and the account changes when the
customer sends back a code the offer produces. That is the whole reason
`users.totp_confirmed_at` exists rather than a boolean.

**The current password gates turning it on and turning it off, and nothing
gates the code check itself.** A stolen session is the caller this refuses:
whoever holds one can read every screen but cannot produce the password, and
both directions matter — arming a second factor on somebody else's account locks
them out of it, and disarming one removes the protection they added.

**A recovery code and a TOTP code arrive through the same field.** The customer
knows they are "the code"; asking them to classify it first is asking them to
understand our schema. Trying TOTP and then the recovery codes costs one argon2
scan on a wrong code, which is what `AUTH_TIER` is for.

**A recovery code still works when the encryption key does not.** If the
deployment's key is missing or wrong, no stored TOTP secret can be opened — and
the codes, which are argon2 hashes and need no key, are then the only way in.
That is not a happy accident; it is why the login path takes an *optional* box
and refuses only the enrolment paths outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth import recovery_codes
from nexovue_api.auth.credentials import CURRENT_PASSWORD_WRONG, verify_password
from nexovue_api.auth.encryption import CredentialBox
from nexovue_api.auth.totp import (
    SecondFactorStore,
    code_matches,
    new_secret,
    provisioning_uri,
)
from nexovue_api.data.postgres.models import User
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError

# What an authenticator app shows above the account. The product's name, not the
# deployment's host: a customer with two installs should still read "nexovue".
ISSUER: Final = "nexovue"

ALREADY_ENABLED: Final = (
    "Two-factor authentication is already on for this account. Turn it off first to enrol "
    "a different authenticator."
)
NOT_ENABLED: Final = "Two-factor authentication is not on for this account."

# One message for a code that is wrong, a code that has already been used, an
# enrolment that expired, and a recovery code that was spent. Telling them apart
# would tell somebody guessing which guesses were once real — the same rule
# `recovery.LINK_NO_LONGER_VALID` carries for links.
CODE_NOT_ACCEPTED: Final = "That code was not accepted. Check your authenticator and try again."

ENROLMENT_EXPIRED: Final = (
    "That enrolment has expired or was never started. Begin again to get a fresh QR code."
)


@dataclass(frozen=True, slots=True)
class EnrolmentOffer:
    """A secret shown once, in both forms a customer can use it in.

    `secret` is for somebody typing it in by hand; `provisioning_uri` is what the
    QR code encodes. Both carry the secret, which is why this is returned by one
    endpoint, once, and by nothing else ever again.
    """

    secret: str
    provisioning_uri: str


@dataclass(frozen=True, slots=True)
class SecondFactorStatus:
    """What the profile screen says about this account."""

    enabled: bool
    confirmed_at: datetime | None
    recovery_codes_remaining: int


def is_enabled(user: User) -> bool:
    """Whether signing in as this user needs a second factor."""
    return user.totp_confirmed_at is not None


async def describe(db: AsyncSession, *, user: User) -> SecondFactorStatus:
    """The state of the account's second factor. Never the secret."""
    return SecondFactorStatus(
        enabled=is_enabled(user),
        confirmed_at=user.totp_confirmed_at,
        recovery_codes_remaining=(
            await recovery_codes.unused_count(db, user_id=user.id) if is_enabled(user) else 0
        ),
    )


async def begin_enrolment(
    store: SecondFactorStore,
    box: CredentialBox,
    *,
    user: User,
    current_password: str,
) -> EnrolmentOffer:
    """Offer a secret. Nothing on the account changes until a code confirms it.

    The secret is sealed before it goes anywhere, so even the ten-minute holding
    place in Redis never sees the base32 string.
    """
    _require_password(user, current_password)
    if is_enabled(user):
        raise ConflictError(ALREADY_ENABLED)

    secret = new_secret()
    await store.offer_enrolment(
        user_id=str(user.id), sealed_secret=box.seal(secret, owner_id=str(user.id))
    )
    return EnrolmentOffer(
        secret=secret,
        provisioning_uri=provisioning_uri(secret, account=user.email, issuer=ISSUER),
    )


async def confirm_enrolment(
    db: AsyncSession,
    store: SecondFactorStore,
    box: CredentialBox,
    *,
    user: User,
    code: str,
) -> tuple[str, ...]:
    """Prove the offer works, arm the factor, and hand back the recovery codes.

    The codes are returned here and never again. A customer who loses them turns
    the factor off with their password and enrols afresh, which is the same
    proof they gave to switch it on.
    """
    if is_enabled(user):
        raise ConflictError(ALREADY_ENABLED)

    pending = await store.take_enrolment(user_id=str(user.id))
    if pending is None:
        raise InvalidRequestError(ENROLMENT_EXPIRED)

    secret = box.open(pending.sealed_secret, owner_id=str(user.id))
    if not code_matches(secret, code):
        # The offer is left open on purpose: a mistyped digit should not send
        # somebody back to a QR code they have already scanned.
        raise InvalidRequestError(CODE_NOT_ACCEPTED)
    # Spent here as well as at sign-in, so the code just used to enrol cannot be
    # replayed within its own step to pass the first challenge.
    await store.claim_step(user_id=str(user.id), code=code)

    user.totp_secret = pending.sealed_secret
    user.totp_confirmed_at = datetime.now(UTC)
    await store.close_enrolment(user_id=str(user.id))

    await recovery_codes.discard_all_for(db, user_id=user.id)
    issued = recovery_codes.issue(user.id)
    db.add_all(issued.rows)
    audit.record_second_factor_enabled(db, user=user)
    return issued.plaintext


async def disable(
    db: AsyncSession,
    store: SecondFactorStore,
    *,
    user: User,
    current_password: str,
) -> None:
    """Turn the factor off and destroy everything that stood for it."""
    _require_password(user, current_password)
    if not is_enabled(user):
        raise InvalidRequestError(NOT_ENABLED)

    user.totp_secret = None
    user.totp_confirmed_at = None
    await recovery_codes.discard_all_for(db, user_id=user.id)
    await store.close_enrolment(user_id=str(user.id))
    audit.record_second_factor_disabled(db, user=user)


async def verify(
    db: AsyncSession,
    store: SecondFactorStore,
    box: CredentialBox | None,
    *,
    user: User,
    code: str,
) -> bool:
    """Whether this code signs the user in. A TOTP code or a recovery code.

    `box` is optional so a deployment whose key is missing still lets a customer
    in with a recovery code rather than stranding every enrolled account.
    """
    if not is_enabled(user):
        return False
    if await _totp_accepts(store, box, user=user, code=code):
        return True
    if await recovery_codes.consume(db, user_id=user.id, submitted=code):
        # Recorded because it is the sentence that tells somebody they have been
        # broken into: a recovery code spent by anybody but its owner is the
        # first visible sign of a takeover that already has the password.
        audit.record_recovery_code_used(db, user=user)
        return True
    return False


async def _totp_accepts(
    store: SecondFactorStore,
    box: CredentialBox | None,
    *,
    user: User,
    code: str,
) -> bool:
    """Whether the authenticator's code is right and has not been used already."""
    if box is None or user.totp_secret is None:
        return False
    if not code_matches(box.open(user.totp_secret, owner_id=str(user.id)), code):
        return False
    # A code stays valid for its whole step, so one read over a shoulder or out
    # of a proxy log is replayable within the minute unless it is claimed.
    return await store.claim_step(user_id=str(user.id), code=code)


def _require_password(user: User, current_password: str) -> None:
    """The gate on arming and disarming. Worded exactly as the profile's own.

    An invited user with no password (F26) is refused identically to a wrong
    one, so this never says which case it is.
    """
    if not verify_password(user.password_hash, current_password):
        raise InvalidRequestError(CURRENT_PASSWORD_WRONG)
