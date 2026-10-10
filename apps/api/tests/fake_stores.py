"""Postgres-backed stores, in a dict, with the real interfaces.

Shared rather than copied into each suite, for the reason `environment.py` is
shared: the alternative is five in-memory second-factor stores drifting apart
from the one they stand in for.

Not named `conftest.py` — another test package already has one, and two modules
of that name are ambiguous to mypy.
"""

from __future__ import annotations

from typing import Any

from nexovue_api.auth.dependencies import get_second_factor_store
from nexovue_api.auth.tokens import ID_VALUED_PURPOSES, EmailChange, TokenPurpose
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

    async def record_failed_attempt(self, *, token: str) -> None:
        challenge = self.challenges.get(token)
        if challenge is None:
            return
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


class FakeOneTimeTokenStore:
    """Mailed links in a dictionary, spent once and only for their purpose.

    For the flows around the store. The store's own guarantees — single use under
    a race, expiry, nothing stored in the clear — are proved against Postgres in
    `test_auth_recovery_live.py`.
    """

    def __init__(self) -> None:
        self.claims: dict[tuple[TokenPurpose, str], tuple[str, str | None]] = {}
        self.lifetimes: list[int] = []
        self.minted = 0

    async def issue(self, *, purpose: TokenPurpose, user_id: str, ttl_seconds: int) -> str:
        if purpose not in ID_VALUED_PURPOSES:
            raise ValueError(f"{purpose.value} carries more than a user id; use its own methods")
        return self._mint(purpose, user_id, None, ttl_seconds)

    async def consume(self, *, purpose: TokenPurpose, token: str) -> str | None:
        if purpose not in ID_VALUED_PURPOSES:
            raise ValueError(f"{purpose.value} carries more than a user id; use its own methods")
        claim = self.claims.pop((purpose, token), None)
        return None if claim is None else claim[0]

    async def issue_email_change(self, *, user_id: str, new_email: str, ttl_seconds: int) -> str:
        return self._mint(TokenPurpose.CHANGE_EMAIL, user_id, new_email, ttl_seconds)

    async def consume_email_change(self, *, token: str) -> EmailChange | None:
        claim = self.claims.pop((TokenPurpose.CHANGE_EMAIL, token), None)
        if claim is None or claim[1] is None:
            return None
        return EmailChange(user_id=claim[0], new_email=claim[1])

    async def revoke_all_for(self, *, purpose: TokenPurpose, user_id: str) -> int:
        spent = [
            key for key, claim in self.claims.items() if key[0] is purpose and claim[0] == user_id
        ]
        for key in spent:
            del self.claims[key]
        return len(spent)

    def _mint(
        self, purpose: TokenPurpose, user_id: str, new_email: str | None, ttl_seconds: int
    ) -> str:
        self.minted += 1
        token = f"token-{self.minted}"
        self.claims[(purpose, token)] = (user_id, new_email)
        self.lifetimes.append(ttl_seconds)
        return token


def use_fake_second_factors(app: Any) -> FakeSecondFactorStore:
    """Point an app's second-factor dependency at a dictionary, and return it.

    Every suite that drives `/auth/login` needs this now that sign-in asks
    whether a second factor is required — the same reason they already replace
    the session store.
    """
    store = FakeSecondFactorStore()
    app.dependency_overrides[get_second_factor_store] = lambda: store
    return store
