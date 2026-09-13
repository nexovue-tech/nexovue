"""The way back in when the phone is gone.

Not to be confused with `recovery.py`, which proves control of an email address.
These are the codes printed once at enrolment and kept in a wallet: a second
factor for somebody who no longer has their first one.

**They hash with argon2, like a password, and not with SHA-256 like an API key.**
`credentials.py` draws that line by entropy — "a password is low-entropy and
chosen by a human, so the cost of a guess is the only defence there is", while a
256-bit random key "gains nothing from stretching". A recovery code sits between
the two and lands on the password side, because of what it is for: it is
transcribed by hand, read off paper, and typed under pressure, so it has to be
short enough for a person to copy without error. Ten characters is fifty bits,
and fifty bits against a fast hash is a weekend on rented hardware. Argon2 is
what makes the shortness affordable.

**The cost of that is a linear scan**, since an argon2 hash cannot be looked up.
A user holds ten codes, so a wrong code costs ten verifications. That is the
right trade at this size and only at this size: the endpoint is on `AUTH_TIER`
and the alternative — an indexable digest — is the one this file just rejected.

**Single use is a column, not a delete.** A spent code is kept with the moment it
was spent, because "one of your recovery codes was used on Tuesday" is the
sentence that tells somebody they have been broken into, and a deleted row
cannot say it. It is never accepted twice.

The plaintext exists for the length of one response. Nothing here logs a code,
returns one after that response, or stores one.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.credentials import hash_password, verify_password
from nexovue_api.data.postgres.models import UserRecoveryCode

# Ten, which is what a person is willing to write down and enough to survive a
# few years of occasional use.
RECOVERY_CODE_COUNT: Final = 10

# Crockford's alphabet without the letters that are read back wrong: no I, L, O
# or U. Somebody copying these by hand is the failure mode that matters, and an
# ambiguous character turns a valid code into a support conversation.
_ALPHABET: Final = "23456789ABCDEFGHJKMNPQRSTVWXYZ"

# Ten characters from a thirty-character alphabet is a little under fifty bits.
CODE_LENGTH: Final = 10

# Shown as `XXXXX-XXXXX`. The hyphen is presentation only — it is stripped before
# anything is hashed or compared, so a customer who types it or omits it is right
# either way.
_GROUP_SIZE: Final = 5
_SEPARATOR: Final = "-"


@dataclass(frozen=True, slots=True)
class IssuedCodes:
    """The plaintext codes to show once, and the rows to store instead."""

    plaintext: tuple[str, ...]
    rows: tuple[UserRecoveryCode, ...]


def issue(user_id: uuid.UUID) -> IssuedCodes:
    """Mint a fresh set. The caller shows the plaintext once and keeps the rows."""
    codes = tuple(_one_code() for _ in range(RECOVERY_CODE_COUNT))
    rows = tuple(
        UserRecoveryCode(user_id=user_id, code_hash=hash_password(_canonical(code)))
        for code in codes
    )
    return IssuedCodes(plaintext=codes, rows=rows)


async def consume(db: AsyncSession, *, user_id: uuid.UUID, submitted: str) -> bool:
    """Spend one unused code of this user's, or answer False.

    The scan stops at the first match, which makes a correct code cheaper than a
    wrong one. That is a timing signal and a deliberately harmless one: it
    reveals that *some* code matched, which the answer already says.
    """
    candidate = _canonical(submitted)
    if not candidate:
        return False

    unused = (
        await db.execute(
            select(UserRecoveryCode).where(
                UserRecoveryCode.user_id == user_id,
                UserRecoveryCode.used_at.is_(None),
            )
        )
    ).scalars()

    for row in unused:
        if verify_password(row.code_hash, candidate):
            row.used_at = datetime.now(UTC)
            return True
    return False


async def discard_all_for(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    """Remove every code a user holds.

    Called when the second factor is turned off and when a new set is issued.
    Deleted rather than marked spent, unlike a code that was used: an unused
    code that is no longer valid has no story to tell, and keeping it would let a
    stale hash outlive the enrolment it belonged to.
    """
    await db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))


async def unused_count(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    """How many codes are still worth something, for the screen to say."""
    counted = await db.scalar(
        select(func.count())
        .select_from(UserRecoveryCode)
        .where(
            UserRecoveryCode.user_id == user_id,
            UserRecoveryCode.used_at.is_(None),
        )
    )
    return int(counted or 0)


def _one_code() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
    return _SEPARATOR.join(
        body[start : start + _GROUP_SIZE] for start in range(0, CODE_LENGTH, _GROUP_SIZE)
    )


def _canonical(code: str) -> str:
    """The form a code is hashed and compared in: upper case, no punctuation or
    spaces. Somebody reading off paper should not be refused for a hyphen."""
    return "".join(character for character in code.upper() if character in _ALPHABET)
