"""Redis-backed stores, in a dict, with the real interfaces.

Shared rather than copied into each suite, for the reason `environment.py` is
shared: the alternative is five in-memory second-factor stores drifting apart
from the one they stand in for.

Not named `conftest.py` — another test package already has one, and two modules
of that name are ambiguous to mypy.
"""

from __future__ import annotations

from typing import Any

from nexovue_api.auth.dependencies import get_second_factor_store
from nexovue_api.auth.totp import MAX_CHALLENGE_ATTEMPTS, Challenge, PendingEnrolment


class FakeSecondFactorStore:
    """Offers, challenges and spent codes in dictionaries.

    The attempt budget and the claim-once rule are reproduced rather than
    stubbed out: both are security properties, and a double that always says
    "yes" to `claim_step` would let a replay test pass against a store that
    never refuses one.
    """

    def __init__(self) -> None:
        self.enrolments: dict[str, bytes] = {}
        self.challenges: dict[str, Challenge] = {}
        self.claimed: set[tuple[str, str]] = set()
        self.minted = 0

    async def offer_enrolment(self, *, user_id: str, sealed_secret: bytes) -> None:
        self.enrolments[user_id] = sealed_secret

    async def take_enrolment(self, *, user_id: str) -> PendingEnrolment | None:
        sealed = self.enrolments.get(user_id)
        return None if sealed is None else PendingEnrolment(user_id=user_id, sealed_secret=sealed)

    async def close_enrolment(self, *, user_id: str) -> None:
        self.enrolments.pop(user_id, None)

    async def open_challenge(self, *, user_id: str) -> str:
        self.minted += 1
        token = f"challenge-{self.minted}"
        self.challenges[token] = Challenge(user_id=user_id, attempts=0)
        return token

    async def read_challenge(self, *, token: str) -> Challenge | None:
        return self.challenges.get(token)

    async def record_failed_attempt(self, *, token: str, challenge: Challenge) -> None:
        if challenge.attempts + 1 >= MAX_CHALLENGE_ATTEMPTS:
            self.challenges.pop(token, None)
            return
        self.challenges[token] = Challenge(
            user_id=challenge.user_id, attempts=challenge.attempts + 1
        )

    async def close_challenge(self, *, token: str) -> None:
        self.challenges.pop(token, None)

    async def claim_step(self, *, user_id: str, code: str) -> bool:
        claim = (user_id, code.strip())
        if claim in self.claimed:
            return False
        self.claimed.add(claim)
        return True


def use_fake_second_factors(app: Any) -> FakeSecondFactorStore:
    """Point an app's second-factor dependency at a dictionary, and return it.

    Every suite that drives `/auth/login` needs this now that sign-in asks
    whether a second factor is required — the same reason they already replace
    the session store.
    """
    store = FakeSecondFactorStore()
    app.dependency_overrides[get_second_factor_store] = lambda: store
    return store
