"""Email normalization and password hashing.

Argon2 for passwords, never SHA-256: a password is low-entropy and chosen by a
human, so the cost of a guess is the only defence there is. API keys are the
opposite case and hash with SHA-256 in `signup/apikeys.py`.

The verification routine here always does the work, even when there is no user
to verify against. An endpoint that returns faster for an unknown email hands an
attacker a free account-enumeration oracle no matter how identical the body is.
"""

from __future__ import annotations

from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError

from nexovue_api.data.postgres.models import EMAIL_MAX_LENGTH

_hasher: Final = PasswordHasher()

# Verified against whenever there is no stored hash, so an unknown email and a
# wrong password cost the same argon2 computation.
_ABSENT_PASSWORD_HASH: Final = _hasher.hash("argon2-timing-equalizer")

MINIMUM_EMAIL_LENGTH: Final = 3

# The refusal every "prove you hold this account" gate gives back — a password
# change, a change of address, and arming or disarming a second factor. One
# wording, here rather than in one of those callers, because it is the same
# refusal and a second phrasing would be a second thing to keep true.
#
# Deliberately identical whether the account has no password at all (an invited
# user, `password_hash` NULL) or the one supplied is wrong. Both are "you did not
# prove you hold the current password", and the remedy in both cases is the
# reset link.
CURRENT_PASSWORD_WRONG: Final = "That is not the current password on this account."


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(stored_hash: str | None, plaintext: str) -> bool:
    """True when the password matches. Costs the same when there is no hash."""
    try:
        _hasher.verify(stored_hash or _ABSENT_PASSWORD_HASH, plaintext)
    except (VerifyMismatchError, Argon2Error):
        return False
    # A user with no password set (invited, never activated) can never sign in,
    # but only after the hash above has been spent.
    return stored_hash is not None


def normalize_email(raw: str) -> str:
    """The form an address is stored and compared in: trimmed and lowercased."""
    return raw.strip().lower()


def is_valid_email(candidate: str) -> bool:
    """A deliberate syntactic check: one @, a dotted domain, no whitespace.

    Not RFC 5322 — that grammar accepts addresses no mail system delivers to,
    and deliverability is proven by sending to it, not by parsing it.
    """
    if not MINIMUM_EMAIL_LENGTH <= len(candidate) <= EMAIL_MAX_LENGTH:
        return False
    if any(character.isspace() for character in candidate):
        return False
    local, separator, domain = candidate.partition("@")
    if not separator or not local or not domain:
        return False
    return "." in domain and not domain.startswith(".") and not domain.endswith(".")
