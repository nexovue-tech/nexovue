"""How long an account under guessing waits (readiness group 9, A1).

Failed sign-ins are counted on the account itself, because the limiter's
counts are per address and per email for a minute — an attacker rotating
addresses gets about 14,400 guesses a day at one account. After
`FAILURES_BEFORE_LOCK` in a row the account refuses for `FIRST_LOCK`, doubling
with each failure after that to `LONGEST_LOCK`, and a full sign-in clears it.

**Always temporary.** A lock that waited for an administrator would let anyone
lock anyone out; this one only slows guessing to a crawl (the shape OWASP cites
from AWS Cognito). Attempts while locked are refused and not counted, so a
guesser cannot keep an account at the ceiling for longer than they guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from nexovue_api.data.postgres.models import User

FAILURES_BEFORE_LOCK: Final = 5
FIRST_LOCK: Final = timedelta(minutes=1)
LONGEST_LOCK: Final = timedelta(minutes=15)


def is_locked(user: User, *, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def record_failure(user: User, *, now: datetime) -> None:
    """One more failure in a row; from the fifth, a lock that doubles."""
    user.failed_logins += 1
    beyond = user.failed_logins - FAILURES_BEFORE_LOCK
    if beyond >= 0:
        user.locked_until = now + min(FIRST_LOCK * 2**beyond, LONGEST_LOCK)


def record_success(user: User) -> None:
    user.failed_logins = 0
    user.locked_until = None
