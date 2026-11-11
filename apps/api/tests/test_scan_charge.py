"""A batch read is charged by the scan, in the buckets the middleware charged (F20, T2.3).

The limiter counts HTTP requests, so a route that runs many scans in one request would
walk around it (research trap 1). `ScanCharge` spends one quota place per later scan in
each bucket the middleware counted the request in, read from `scope["state"]`, so the
route and the middleware cannot disagree about whose allowance pays (design section 6).

Through the real middleware and the real limiter, as `test_ratelimit_callers.py` is:
AC16 (N scans spend N in both buckets, the same keys), AC17 (refused midway, the rest
still answered), AC60 (limits off charge nothing), and the arrival the read's deadline
counts from (D25), recorded even with limits off.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Final, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nexovue_api.auth.sessions import Session, SessionStore
from nexovue_api.middleware.ratelimit import (
    SCOPE_ORGANIZATION,
    SCOPE_SESSION,
    RateLimitMiddleware,
)
from nexovue_api.ratelimit.charge import (
    Refused,
    ScanCharge,
    arrival_of,
    scan_charge_for,
)
from nexovue_api.ratelimit.limiter import RateLimiter
from nexovue_api.ratelimit.policy import TELEMETRY_TIER, FixedPlanLimits
from nexovue_api.ratelimit.token_owners import TokenOwners
from nexovue_api.settings import API_PREFIX

BATCH: Final = f"{API_PREFIX}/dashboard-panels/read"
PER_PERSON: Final = TELEMETRY_TIER.quota.limit
assert TELEMETRY_TIER.org_quota is not None
PER_ORGANIZATION: Final = TELEMETRY_TIER.org_quota.limit


class _Sessions:
    def __init__(self) -> None:
        self.by_token: dict[str, Session] = {}

    async def peek(self, token: str) -> Session | None:
        return self.by_token.get(token)


class _Batch:
    """An app whose one route claims `scans` scans through `ScanCharge`, as the batch does."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.sessions = _Sessions()
        self.limiter = RateLimiter(cast(async_sessionmaker[AsyncSession], None), instances=1)
        self.outcomes: list[Refused | None] = []
        self.arrived_at: float | None = None

        async def batch(request: Request) -> JSONResponse:
            scans = int(request.query_params["scans"])
            charge = scan_charge_for(request.scope, self.limiter)
            self.arrived_at = arrival_of(request.scope)
            self.outcomes = [await charge.claim() for _ in range(scans)]
            return JSONResponse({"ok": True})

        self.app = RateLimitMiddleware(
            Starlette(routes=[Route(BATCH, batch)]),
            limiter=self.limiter,
            plan_limits=FixedPlanLimits(),
            enabled=enabled,
            sessions=cast(SessionStore, self.sessions),
            token_owners=cast(TokenOwners, None),
        )

    def person(self, org_id: str) -> tuple[str, str]:
        token, user_id = f"session-{uuid.uuid4()}", str(uuid.uuid4())
        self.sessions.by_token[token] = Session(
            user_id=user_id, org_id=org_id, created_at=datetime.now(UTC)
        )
        return token, user_id

    async def read(self, token: str, scans: int) -> int:
        transport = httpx.ASGITransport(app=self.app, client=("10.0.0.1", 40_000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                BATCH, params={"scans": scans}, headers={"Authorization": f"Bearer {token}"}
            )
        return response.status_code

    async def remaining(self, scope: str, identity: str, limit: int) -> int:
        """What is left in a bucket, read by spending one more place there."""
        quota = TELEMETRY_TIER.quota if scope == SCOPE_SESSION else TELEMETRY_TIER.org_quota
        assert quota is not None and quota.limit == limit
        decision = await self.limiter.consume(scope=scope, identity=identity, quota=quota)
        return decision.remaining + 1


async def test_ac16_n_scans_spend_n_places_in_both_buckets_under_the_middlewares_keys() -> None:
    batch = _Batch()
    org_id = str(uuid.uuid4())
    token, user_id = batch.person(org_id)

    assert await batch.read(token, scans=7) == 200

    assert batch.outcomes == [None] * 7
    # The request's own place paid for the first scan; six more were spent, in the
    # buckets the middleware keyed — the person's, then the organization's.
    assert await batch.remaining(SCOPE_SESSION, f"user:{user_id}", PER_PERSON) == PER_PERSON - 7
    assert await batch.remaining(SCOPE_ORGANIZATION, org_id, PER_ORGANIZATION) == (
        PER_ORGANIZATION - 7
    )


async def test_ac17_a_person_with_three_places_left_gets_three_scans_and_a_wait() -> None:
    batch = _Batch()
    org_id = str(uuid.uuid4())
    token, user_id = batch.person(org_id)
    for _ in range(PER_PERSON - 3):
        await batch.limiter.consume(
            scope=SCOPE_SESSION, identity=f"user:{user_id}", quota=TELEMETRY_TIER.quota
        )

    # The request takes one place and its first scan rides on it; two more fit.
    assert await batch.read(token, scans=10) == 200

    assert batch.outcomes[:3] == [None, None, None]
    refused = batch.outcomes[3:]
    assert len(refused) == 7
    assert all(isinstance(outcome, Refused) for outcome in refused)
    assert all(outcome is not None and outcome.retry_after_seconds >= 1 for outcome in refused)


async def test_ac60_limits_off_charge_nothing_and_refuse_no_scan() -> None:
    batch = _Batch(enabled=False)
    token, _ = batch.person(str(uuid.uuid4()))

    assert await batch.read(token, scans=PER_PERSON * 2) == 200

    assert batch.outcomes == [None] * (PER_PERSON * 2)


async def test_the_arrival_is_recorded_before_any_wait_even_with_limits_off() -> None:
    """D25: the read's deadline counts from arrival, so every wait shortens its scans."""
    for enabled in (True, False):
        batch = _Batch(enabled=enabled)
        token, _ = batch.person(str(uuid.uuid4()))
        before = time.monotonic()

        assert await batch.read(token, scans=1) == 200

        assert batch.arrived_at is not None
        assert before <= batch.arrived_at <= time.monotonic()


def test_a_scope_the_middleware_never_saw_is_charged_nothing() -> None:
    """No `counted` means limits are off (D43): the off switch has one implementation."""
    charge = scan_charge_for({"type": "http"}, cast(RateLimiter, None))

    assert isinstance(charge, ScanCharge)
    assert arrival_of({"type": "http", "state": {}}) is None
