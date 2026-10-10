"""Time-based one-time passwords, and the short-lived state around them.

**PyOTP rather than a hand-rolled HMAC.** RFC 6238 is short enough to implement
and long enough to get wrong in ways nothing tests: the counter's byte order, the
dynamic-truncation offset, the constant-time compare. PyOTP is the reference
Python implementation (`pyauth/pyotp`, MIT, 2.10.0 released 2026-06-14, and
current for Python 3.13), and its own documentation carries the three rules that
are *not* in the library — reject a code that has already been used, rate-limit
the endpoint, and keep the secret in a controlled store — each of which is
answered here or in `ratelimit/policy.py`.

PyOTP's guidance also says a greenfield product should consider WebAuthn instead,
and it is right that a phishable shared secret is the weaker factor. TOTP is what
T64 asks for and what a customer can enrol today with a phone they already own;
WebAuthn is a larger decision about hardware and recovery, and is not made here.

**Three pieces of short-lived state, each a table of its own** (ADR-020).

*A pending enrolment.* A secret that has been shown but not yet confirmed is not
a credential on the account — it is an offer. Keeping it out of `users` means an
abandoned enrolment lapses on its own rather than leaving a half-armed second
factor on a row, which is the same reasoning `tokens.py` gives for reset links.

*A login challenge.* Between "the password was right" and "the code was right"
there is a caller who has proved one factor of two. That is a short-lived,
single-account claim — a session's shape. It carries an attempt budget, so a
typo costs an attempt rather than the whole sign-in, and five wrong codes end it.

*A spent code.* A TOTP code stays valid for its whole step, so one observed over a
shoulder or out of a proxy log can be replayed within the minute. Marking it
used the first time closes that, and the marker is worthless ninety seconds later.

**Each write is its own transaction, not the request's.** A wrong code answers
401, which rolls the request back; a spent attempt or a claimed code that rolled
back with it would be a budget that is never spent.

Every row is keyed by the fingerprint of its token or code, never the value, for
the reason `sessions.py` states.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import pyotp
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexovue_api.auth.sessions import token_fingerprint
from nexovue_api.data.postgres.models import TotpChallenge, TotpEnrolment, TotpSpentCode

# RFC 6238's defaults, and what every authenticator app assumes. Stated rather
# than left implicit because changing one silently breaks every enrolled device.
TOTP_DIGITS: Final = 6
TOTP_INTERVAL_SECONDS: Final = 30

# One step either side of now. Phone clocks drift and people finish typing late;
# a window of zero rejects honest codes, and a wider one lengthens how long a
# stolen code stays useful. One step is the usual answer and the one PyOTP's own
# documentation demonstrates.
TOTP_VALID_WINDOW: Final = 1

# 160 bits, the RFC 4226 recommendation for an HMAC-SHA1 shared secret, which is
# what an authenticator app expects. Base32 by `pyotp.random_base32`.
SECRET_BASE32_LENGTH: Final = 32

# Long enough to install an app, scan the code and wait for the next one; short
# enough that an offer nobody took is gone before it is forgotten.
PENDING_ENROLMENT_TTL_SECONDS: Final = 10 * 60

# The gap between the two halves of a sign-in. It is a partial credential, so it
# lives about as long as it takes to read a code off a phone.
CHALLENGE_TTL_SECONDS: Final = 5 * 60

# What a challenge is worth before it is thrown away. Five is generous for
# somebody copying six digits and miserly for somebody guessing them: the space
# is a million wide, so five attempts per password submission is not a search.
MAX_CHALLENGE_ATTEMPTS: Final = 5

# A step is valid for its own interval plus the window either side. The marker
# only has to outlive the code it refuses.
SPENT_CODE_TTL_SECONDS: Final = TOTP_INTERVAL_SECONDS * (2 * TOTP_VALID_WINDOW + 1)

CHALLENGE_TOKEN_BYTES: Final = 32


def new_secret() -> str:
    """A fresh base32 shared secret, in the form an authenticator app scans."""
    return pyotp.random_base32(length=SECRET_BASE32_LENGTH)


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """The `otpauth://` URI behind the QR code.

    It carries the secret, which is why it is returned exactly once, from the
    enrolment endpoint, and never afterwards.
    """
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_SECONDS).provisioning_uri(
        name=account, issuer_name=issuer
    )


def code_matches(secret: str, code: str) -> bool:
    """Whether a code is one this secret produces around now.

    PyOTP compares in constant time. A malformed code is simply not a match —
    the caller must not be told the difference between "not six digits" and
    "wrong six digits", because one of those answers narrows a guess.
    """
    if not code:
        return False
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_SECONDS)
    return bool(totp.verify(code.strip(), valid_window=TOTP_VALID_WINDOW))


@dataclass(frozen=True, slots=True)
class PendingEnrolment:
    """A secret offered to one user and not yet confirmed by them."""

    user_id: str
    sealed_secret: bytes


@dataclass(frozen=True, slots=True)
class Challenge:
    """A sign-in that has passed its first factor and not yet its second."""

    user_id: str
    attempts: int


class SecondFactorStore:
    """The short-lived half of second-factor authentication."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def offer_enrolment(self, *, user_id: str, sealed_secret: bytes) -> None:
        """Hold a sealed, unconfirmed secret for one user.

        One offer at a time, overwriting any earlier one: a customer who restarts
        enrolment is looking at the QR code on their screen now, and the secret
        behind the tab they closed must stop being confirmable.
        """
        expires_at = _after(PENDING_ENROLMENT_TTL_SECONDS)
        offer = insert(TotpEnrolment).values(
            user_id=uuid.UUID(user_id), sealed_secret=sealed_secret, expires_at=expires_at
        )
        async with self._session_factory.begin() as db:
            await db.execute(
                offer.on_conflict_do_update(
                    index_elements=[TotpEnrolment.user_id],
                    set_={"sealed_secret": sealed_secret, "expires_at": expires_at},
                )
            )

    async def take_enrolment(self, *, user_id: str) -> PendingEnrolment | None:
        """The offer for this user, if it is still open. Does not spend it.

        Confirmation reads it, checks the code, and only then removes it —
        spending it on a wrong code would send somebody back to a QR code they
        had already scanned.
        """
        async with self._session_factory() as db:
            sealed = await db.scalar(
                select(TotpEnrolment.sealed_secret).where(
                    TotpEnrolment.user_id == uuid.UUID(user_id),
                    TotpEnrolment.expires_at > datetime.now(UTC),
                )
            )
        return None if sealed is None else PendingEnrolment(user_id=user_id, sealed_secret=sealed)

    async def close_enrolment(self, *, user_id: str) -> None:
        """Forget the offer, whether it was taken up or abandoned."""
        async with self._session_factory.begin() as db:
            await db.execute(
                delete(TotpEnrolment).where(TotpEnrolment.user_id == uuid.UUID(user_id))
            )

    async def open_challenge(self, *, user_id: str) -> str:
        """Mint the token that stands for "this password was right"."""
        token = secrets.token_urlsafe(CHALLENGE_TOKEN_BYTES)
        async with self._session_factory.begin() as db:
            await db.execute(
                insert(TotpChallenge).values(
                    token_hash=token_fingerprint(token),
                    user_id=uuid.UUID(user_id),
                    expires_at=_after(CHALLENGE_TTL_SECONDS),
                )
            )
        return token

    async def read_challenge(self, *, token: str) -> Challenge | None:
        """Who a challenge names, or None for every way it can fail.

        One answer for never-issued, expired, spent and exhausted, on the same
        reasoning `tokens.consume` carries: telling them apart tells an attacker
        which guesses were once real.
        """
        if not token:
            return None
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(TotpChallenge.user_id, TotpChallenge.attempts).where(
                        TotpChallenge.token_hash == token_fingerprint(token),
                        TotpChallenge.expires_at > datetime.now(UTC),
                    )
                )
            ).first()
        return None if row is None else Challenge(user_id=str(row.user_id), attempts=row.attempts)

    async def record_failed_attempt(self, *, token: str) -> None:
        """Spend one of a challenge's attempts, ending it at the last.

        One increment in the database rather than a read and a write, so two
        wrong codes sent together spend two attempts. `expires_at` is untouched:
        a wrong code must not be a way to keep a half-finished sign-in alive.
        """
        fingerprint = token_fingerprint(token)
        async with self._session_factory.begin() as db:
            attempts = await db.scalar(
                update(TotpChallenge)
                .where(TotpChallenge.token_hash == fingerprint)
                .values(attempts=TotpChallenge.attempts + 1)
                .returning(TotpChallenge.attempts)
            )
            if attempts is not None and attempts >= MAX_CHALLENGE_ATTEMPTS:
                await db.execute(
                    delete(TotpChallenge).where(TotpChallenge.token_hash == fingerprint)
                )

    async def close_challenge(self, *, token: str) -> None:
        """End a challenge, spent or abandoned."""
        async with self._session_factory.begin() as db:
            await db.execute(
                delete(TotpChallenge).where(TotpChallenge.token_hash == token_fingerprint(token))
            )

    async def claim_step(self, *, user_id: str, code: str) -> bool:
        """Claim a code as used, returning False if it already was.

        Keyed on the code rather than on the step number because the code is
        what an eavesdropper replays. Only the fingerprint is stored, so the
        marker is not itself a copy of a live credential. A lapsed marker is
        replaced rather than refusing, since the sweep may not have reached it.
        """
        now = datetime.now(UTC)
        claim = insert(TotpSpentCode).values(
            user_id=uuid.UUID(user_id),
            code_hash=token_fingerprint(code),
            expires_at=now + timedelta(seconds=SPENT_CODE_TTL_SECONDS),
        )
        async with self._session_factory.begin() as db:
            claimed = await db.scalar(
                claim.on_conflict_do_update(
                    index_elements=[TotpSpentCode.user_id, TotpSpentCode.code_hash],
                    set_={"expires_at": claim.excluded.expires_at},
                    where=TotpSpentCode.expires_at <= now,
                ).returning(TotpSpentCode.code_hash)
            )
        return claimed is not None


def _after(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)
