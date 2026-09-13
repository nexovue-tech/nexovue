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

**Three pieces of state live in Redis, and each is here because Postgres is the
wrong place for it.**

*A pending enrolment.* A secret that has been shown but not yet confirmed is not
a credential on the account — it is an offer. Keeping it out of `users` means an
abandoned enrolment expires on its own rather than leaving a half-armed second
factor on a row, which is the same reasoning `tokens.py` gives for reset links.

*A login challenge.* Between "the password was right" and "the code was right"
there is a caller who has proved one factor of two. That is a short-lived,
single-account claim — a session's shape, not a row's. It carries an attempt
budget, so a typo costs an attempt rather than the whole sign-in, and five wrong
codes end it.

*A spent code.* A TOTP code stays valid for its whole step, so one observed over a
shoulder or out of a proxy log can be replayed within the minute. Marking the
step used the first time closes that, and the marker is worthless thirty seconds
later — which is a TTL, not a column.

Every key is the fingerprint of its token, never the token, for the reason
`sessions.py` states: a dump of Redis should be expiry times and not credentials.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Final

import pyotp
from redis.asyncio import Redis

from nexovue_api.auth.sessions import token_fingerprint

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

PENDING_KEY_PREFIX: Final = "totp-enrolment"
CHALLENGE_KEY_PREFIX: Final = "totp-challenge"
SPENT_STEP_KEY_PREFIX: Final = "totp-step"

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
_SPENT_STEP_TTL_SECONDS: Final = TOTP_INTERVAL_SECONDS * (2 * TOTP_VALID_WINDOW + 1)

CHALLENGE_TOKEN_BYTES: Final = 32

_USER_ID: Final = "user_id"
_SECRET: Final = "secret"
_ATTEMPTS: Final = "attempts"


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
    """The Redis half of second-factor authentication."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def offer_enrolment(self, *, user_id: str, sealed_secret: bytes) -> None:
        """Hold a sealed, unconfirmed secret for one user.

        One offer at a time, overwriting any earlier one: a customer who restarts
        enrolment is looking at the QR code on their screen now, and the secret
        behind the tab they closed must stop being confirmable.
        """
        await self._redis.set(
            _pending_key(user_id), sealed_secret, ex=PENDING_ENROLMENT_TTL_SECONDS
        )

    async def take_enrolment(self, *, user_id: str) -> PendingEnrolment | None:
        """The offer for this user, if it is still open. Does not spend it.

        Confirmation reads it, checks the code, and only then removes it —
        spending it on a wrong code would send somebody back to a QR code they
        had already scanned.
        """
        sealed = await self._redis.get(_pending_key(user_id))
        if sealed is None:
            return None
        return PendingEnrolment(user_id=user_id, sealed_secret=_as_bytes(sealed))

    async def close_enrolment(self, *, user_id: str) -> None:
        """Forget the offer, whether it was taken up or abandoned."""
        await self._redis.delete(_pending_key(user_id))

    async def open_challenge(self, *, user_id: str) -> str:
        """Mint the token that stands for "this password was right"."""
        token = secrets.token_urlsafe(CHALLENGE_TOKEN_BYTES)
        await self._redis.set(
            _challenge_key(token),
            json.dumps({_USER_ID: user_id, _ATTEMPTS: 0}),
            ex=CHALLENGE_TTL_SECONDS,
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
        raw = await self._redis.get(_challenge_key(token))
        return _decoded_challenge(raw) if raw is not None else None

    async def record_failed_attempt(self, *, token: str, challenge: Challenge) -> None:
        """Spend one of a challenge's attempts, ending it at the last.

        The TTL is preserved rather than restarted: a wrong code must not be a
        way to keep a half-finished sign-in alive indefinitely.
        """
        if challenge.attempts + 1 >= MAX_CHALLENGE_ATTEMPTS:
            await self.close_challenge(token=token)
            return
        await self._redis.set(
            _challenge_key(token),
            json.dumps({_USER_ID: challenge.user_id, _ATTEMPTS: challenge.attempts + 1}),
            keepttl=True,
        )

    async def close_challenge(self, *, token: str) -> None:
        """End a challenge, spent or abandoned."""
        await self._redis.delete(_challenge_key(token))

    async def claim_step(self, *, user_id: str, code: str) -> bool:
        """Claim a code as used, returning False if it already was.

        Keyed on the code rather than on the step number because the code is
        what an eavesdropper replays, and it is what identifies the step to
        anybody who has it. Only the fingerprint is stored, so the marker is not
        itself a copy of a live credential.
        """
        claimed = await self._redis.set(
            _spent_step_key(user_id, code), b"1", ex=_SPENT_STEP_TTL_SECONDS, nx=True
        )
        return bool(claimed)


def _pending_key(user_id: str) -> str:
    return f"{PENDING_KEY_PREFIX}:{user_id}"


def _challenge_key(token: str) -> str:
    return f"{CHALLENGE_KEY_PREFIX}:{token_fingerprint(token)}"


def _spent_step_key(user_id: str, code: str) -> str:
    return f"{SPENT_STEP_KEY_PREFIX}:{user_id}:{token_fingerprint(code)}"


def _as_bytes(value: str | bytes) -> bytes:
    return value if isinstance(value, bytes) else value.encode()


def _decoded_challenge(raw: str | bytes) -> Challenge | None:
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        payload: Any = json.loads(text)
        return Challenge(user_id=payload[_USER_ID], attempts=int(payload[_ATTEMPTS]))
    except (ValueError, KeyError, TypeError):
        # A value we cannot read is a claim we cannot trust.
        return None
