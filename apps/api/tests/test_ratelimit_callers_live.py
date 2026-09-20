"""Who a request is counted against, through the real middleware and real Redis.

The defect (`docs/changes/rate-limit-per-person/`): every request was counted
against its network address first, so a team in one office — several people
behind one address — shared one person's read budget. Signed-in reads are now
counted per person under a per-organization ceiling (founder, 2026-09-20);
signed-out requests, and tokens that resolve to no session, stay per address.

Each test uses its own address and its own organization, so the counters it
fills are its own. This suite fails rather than skips when Redis is down.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import httpx
import pytest
from live import require_stack
from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from nexovue_api.apitokens.tokens import API_TOKEN_PREFIX
from nexovue_api.auth.sessions import SessionStore
from nexovue_api.middleware.ratelimit import RateLimitMiddleware
from nexovue_api.ratelimit.headers import POLICY_HEADER, RETRY_AFTER_HEADER, STATE_HEADER
from nexovue_api.ratelimit.limiter import RateLimiter
from nexovue_api.ratelimit.policy import TELEMETRY_TIER, TOKEN_TIER, FixedPlanLimits
from nexovue_api.settings import API_PREFIX

REDIS_URL: Final = os.environ.get("NEXOVUE_REDIS_URL", "redis://localhost:16379/0")
READ: Final = f"{API_PREFIX}/traces"
PER_PERSON: Final = TELEMETRY_TIER.quota.limit


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 6379), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(REDIS_URL),
    suite="apps/api/tests/test_ratelimit_callers_live.py",
    remedy="Redis is not reachable — run `just up`",
)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(REDIS_URL)
    try:
        yield client
    finally:
        await client.aclose()


def _app(redis: Redis) -> RateLimitMiddleware:
    async def read(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route(READ, read)])
    return RateLimitMiddleware(
        inner,
        limiter=RateLimiter(redis),
        plan_limits=FixedPlanLimits(),
        sessions=SessionStore(redis, ttl_seconds=600),
    )


def _client(app: RateLimitMiddleware, address: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(address, 40_000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _address() -> str:
    """One office's public address, unique to the test so its counters are its own."""
    octets = uuid.uuid4().bytes
    return f"10.{octets[0]}.{octets[1]}.{octets[2]}"


async def _person(redis: Redis, org_id: str) -> str:
    token, _ = await SessionStore(redis, ttl_seconds=600).create(
        user_id=str(uuid.uuid4()), org_id=org_id
    )
    return token


async def _reads(client: httpx.AsyncClient, token: str | None, count: int) -> list[int]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return [(await client.get(READ, headers=headers)).status_code for _ in range(count)]


async def test_colleagues_behind_one_address_each_have_their_own_budget(redis: Redis) -> None:
    """The defect: the second colleague was refused on the first one's reads."""
    org_id = str(uuid.uuid4())
    first, second = await _person(redis, org_id), await _person(redis, org_id)
    async with _client(_app(redis), _address()) as office:
        spent = await _reads(office, first, PER_PERSON)
        assert set(spent) == {200}

        assert await _reads(office, second, 1) == [200]


async def test_one_person_is_still_held_to_their_own_budget(redis: Redis) -> None:
    person = await _person(redis, str(uuid.uuid4()))
    async with _client(_app(redis), _address()) as office:
        assert set(await _reads(office, person, PER_PERSON)) == {200}
        assert await _reads(office, person, 1) == [429]


async def test_an_organization_is_held_to_its_ceiling_however_many_people_read(
    redis: Redis,
) -> None:
    """The ceiling protects ClickHouse from one tenant, not from one person."""
    ceiling = TELEMETRY_TIER.org_quota
    assert ceiling is not None
    org_id = str(uuid.uuid4())
    people = [await _person(redis, org_id) for _ in range(ceiling.limit // PER_PERSON + 1)]
    async with _client(_app(redis), _address()) as office:
        answered = [code for person in people for code in await _reads(office, person, PER_PERSON)]

    assert answered.count(200) == ceiling.limit
    assert 429 in answered


async def test_another_organization_is_not_held_to_this_ones_ceiling(redis: Redis) -> None:
    ceiling = TELEMETRY_TIER.org_quota
    assert ceiling is not None
    busy = str(uuid.uuid4())
    people = [await _person(redis, busy) for _ in range(ceiling.limit // PER_PERSON + 1)]
    elsewhere = await _person(redis, str(uuid.uuid4()))
    async with _client(_app(redis), _address()) as office:
        for person in people:
            await _reads(office, person, PER_PERSON)
        assert await _reads(office, elsewhere, 1) == [200]


async def test_an_invented_token_is_counted_against_its_address(redis: Redis) -> None:
    """A token that names no session buys nothing: its caller stays on the address."""
    async with _client(_app(redis), _address()) as stranger:
        answered = [
            code
            for _ in range(PER_PERSON + 1)
            for code in await _reads(stranger, f"invented-{uuid.uuid4()}", 1)
        ]

    assert answered[:PER_PERSON] == [200] * PER_PERSON
    assert answered[-1] == 429


async def test_signed_out_reads_are_counted_against_their_address(redis: Redis) -> None:
    async with _client(_app(redis), _address()) as anonymous:
        assert set(await _reads(anonymous, None, PER_PERSON)) == {200}
        assert await _reads(anonymous, None, 1) == [429]


def _a_token() -> str:
    """A token-shaped value. The middleware counts by fingerprint, never by row."""
    return f"{API_TOKEN_PREFIX}{uuid.uuid4().hex}"


async def test_a_token_spends_its_own_budget_and_not_the_address_it_dialled_from(
    redis: Redis,
) -> None:
    """FR19, AC19. One CI runner may hold several tokens; one must not spend another."""
    first, second = _a_token(), _a_token()
    async with _client(_app(redis), _address()) as runner:
        assert set(await _reads(runner, first, TOKEN_TIER.quota.limit)) == {200}
        assert await _reads(runner, first, 1) == [429]

        # The address is spent; the second token is not.
        assert await _reads(runner, second, 1) == [200]


async def test_a_refused_token_is_told_the_policy_and_when_to_retry(redis: Redis) -> None:
    """AC19: the documented headers, on the answer that refuses."""
    token = _a_token()
    async with _client(_app(redis), _address()) as runner:
        await _reads(runner, token, TOKEN_TIER.quota.limit)
        refused = await runner.get(READ, headers={"Authorization": f"Bearer {token}"})

    assert refused.status_code == 429
    assert TOKEN_TIER.quota.name in refused.headers[POLICY_HEADER]
    assert TOKEN_TIER.quota.name in refused.headers[STATE_HEADER]
    assert int(refused.headers[RETRY_AFTER_HEADER]) >= 1
    assert refused.json()["violated-policies"] == [TOKEN_TIER.quota.name]


async def test_a_persons_reads_are_not_spent_by_a_token_at_the_same_desk(redis: Redis) -> None:
    person = await _person(redis, str(uuid.uuid4()))
    token = _a_token()
    async with _client(_app(redis), _address()) as desk:
        await _reads(desk, token, TOKEN_TIER.quota.limit)

        assert await _reads(desk, person, PER_PERSON) == [200] * PER_PERSON
