"""Who a request is counted against, through the real middleware and limiter.

The defect (`docs/changes/rate-limit-per-person/`): every request was counted
against its network address first, so a team in one office — several people
behind one address — shared one person's read budget. Signed-in reads are now
counted per person under a per-organization ceiling (founder, 2026-09-20);
signed-out requests, and tokens that resolve to no session, stay per address.

These tiers are counted in the API process's own memory (ADR-020), so nothing
here needs a running store: each test builds its own limiter. The auth tier,
which is counted in Postgres, is `test_auth_limit_live.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Final, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from nexovue_api.apitokens.tokens import API_TOKEN_PREFIX
from nexovue_api.auth.sessions import Session, SessionStore
from nexovue_api.middleware.ratelimit import TOO_MANY_CONCURRENT_DETAIL, RateLimitMiddleware
from nexovue_api.ratelimit.headers import POLICY_HEADER, RETRY_AFTER_HEADER, STATE_HEADER
from nexovue_api.ratelimit.limiter import RateLimiter
from nexovue_api.ratelimit.policy import TELEMETRY_TIER, TOKEN_TIER, FixedPlanLimits
from nexovue_api.ratelimit.token_owners import TokenOwners
from nexovue_api.settings import API_PREFIX

READ: Final = f"{API_PREFIX}/traces"
SLOW_READ: Final = f"{API_PREFIX}/traces/slow"
PER_PERSON: Final = TELEMETRY_TIER.quota.limit


class _Sessions:
    """Signed-in people by token: all the middleware asks a session store."""

    def __init__(self) -> None:
        self.by_token: dict[str, Session] = {}

    async def peek(self, token: str) -> Session | None:
        return self.by_token.get(token)


class _Owners:
    """Which organization each API token belongs to."""

    def __init__(self) -> None:
        self.by_token: dict[str, str] = {}

    async def organization_of(self, presented: str) -> str | None:
        return self.by_token.get(presented)


class _World:
    """One app under test, its people and tokens, and a gate slow reads wait at."""

    def __init__(self) -> None:
        self.sessions = _Sessions()
        self.owners = _Owners()
        self.released = asyncio.Event()

        async def read(_: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        async def slow_read(_: Request) -> PlainTextResponse:
            await self.released.wait()
            return PlainTextResponse("ok")

        inner = Starlette(routes=[Route(READ, read), Route(SLOW_READ, slow_read)])
        # No exact quota is spent on these tiers, so the factory is never opened.
        unused = cast(async_sessionmaker[AsyncSession], None)
        self.app = RateLimitMiddleware(
            inner,
            limiter=RateLimiter(unused, instances=1),
            plan_limits=FixedPlanLimits(),
            sessions=cast(SessionStore, self.sessions),
            token_owners=cast(TokenOwners, self.owners),
        )

    def person(self, org_id: str) -> str:
        token = f"session-{uuid.uuid4()}"
        self.sessions.by_token[token] = Session(
            user_id=str(uuid.uuid4()), org_id=org_id, created_at=datetime.now(UTC)
        )
        return token

    def token(self, org_id: str | None = None) -> str:
        """A token-shaped value, owned by `org_id` when one is given."""
        token = f"{API_TOKEN_PREFIX}{uuid.uuid4().hex}"
        if org_id is not None:
            self.owners.by_token[token] = org_id
        return token


def _client(world: _World, address: str | None = None) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=world.app, client=(address or _address(), 40_000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _address() -> str:
    """One office's public address, unique to the test so its counters are its own."""
    octets = uuid.uuid4().bytes
    return f"10.{octets[0]}.{octets[1]}.{octets[2]}"


async def _reads(client: httpx.AsyncClient, token: str | None, count: int) -> list[int]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return [(await client.get(READ, headers=headers)).status_code for _ in range(count)]


async def test_colleagues_behind_one_address_each_have_their_own_budget() -> None:
    """The defect: the second colleague was refused on the first one's reads."""
    world = _World()
    org_id = str(uuid.uuid4())
    first, second = world.person(org_id), world.person(org_id)
    async with _client(world) as office:
        spent = await _reads(office, first, PER_PERSON)
        assert set(spent) == {200}

        assert await _reads(office, second, 1) == [200]


async def test_one_person_is_still_held_to_their_own_budget() -> None:
    world = _World()
    person = world.person(str(uuid.uuid4()))
    async with _client(world) as office:
        assert set(await _reads(office, person, PER_PERSON)) == {200}
        assert await _reads(office, person, 1) == [429]


async def test_an_organization_is_held_to_its_ceiling_however_many_people_read() -> None:
    """The ceiling protects ClickHouse from one tenant, not from one person."""
    world = _World()
    ceiling = TELEMETRY_TIER.org_quota
    assert ceiling is not None
    org_id = str(uuid.uuid4())
    people = [world.person(org_id) for _ in range(ceiling.limit // PER_PERSON + 1)]
    async with _client(world) as office:
        answered = [code for person in people for code in await _reads(office, person, PER_PERSON)]

    assert answered.count(200) == ceiling.limit
    assert 429 in answered


async def test_another_organization_is_not_held_to_this_ones_ceiling() -> None:
    world = _World()
    ceiling = TELEMETRY_TIER.org_quota
    assert ceiling is not None
    busy = str(uuid.uuid4())
    people = [world.person(busy) for _ in range(ceiling.limit // PER_PERSON + 1)]
    elsewhere = world.person(str(uuid.uuid4()))
    async with _client(world) as office:
        for person in people:
            await _reads(office, person, PER_PERSON)
        assert await _reads(office, elsewhere, 1) == [200]


async def test_an_invented_token_is_counted_against_its_address() -> None:
    """A token that names no session buys nothing: its caller stays on the address."""
    world = _World()
    async with _client(world) as stranger:
        answered = [
            code
            for _ in range(PER_PERSON + 1)
            for code in await _reads(stranger, f"invented-{uuid.uuid4()}", 1)
        ]

    assert answered[:PER_PERSON] == [200] * PER_PERSON
    assert answered[-1] == 429


async def test_signed_out_reads_are_counted_against_their_address() -> None:
    world = _World()
    async with _client(world) as anonymous:
        assert set(await _reads(anonymous, None, PER_PERSON)) == {200}
        assert await _reads(anonymous, None, 1) == [429]


async def test_a_token_spends_its_own_budget_and_not_the_address_it_dialled_from() -> None:
    """FR19, AC19. One CI runner may hold several tokens; one must not spend another."""
    world = _World()
    first, second = world.token(), world.token()
    async with _client(world) as runner:
        assert set(await _reads(runner, first, TOKEN_TIER.quota.limit)) == {200}
        assert await _reads(runner, first, 1) == [429]

        # The address is spent; the second token is not.
        assert await _reads(runner, second, 1) == [200]


async def test_a_refused_token_is_told_the_policy_and_when_to_retry() -> None:
    """AC19: the documented headers, on the answer that refuses."""
    world = _World()
    token = world.token()
    async with _client(world) as runner:
        await _reads(runner, token, TOKEN_TIER.quota.limit)
        refused = await runner.get(READ, headers={"Authorization": f"Bearer {token}"})

    assert refused.status_code == 429
    assert TOKEN_TIER.quota.name in refused.headers[POLICY_HEADER]
    assert TOKEN_TIER.quota.name in refused.headers[STATE_HEADER]
    assert int(refused.headers[RETRY_AFTER_HEADER]) >= 1
    assert refused.json()["violated-policies"] == [TOKEN_TIER.quota.name]


async def test_a_persons_reads_are_not_spent_by_a_token_at_the_same_desk() -> None:
    world = _World()
    person = world.person(str(uuid.uuid4()))
    token = world.token()
    async with _client(world) as desk:
        await _reads(desk, token, TOKEN_TIER.quota.limit)

        assert await _reads(desk, person, PER_PERSON) == [200] * PER_PERSON


async def test_an_organizations_tokens_share_one_in_flight_ceiling() -> None:
    """Minting more tokens must not multiply what one tenant may have scanning at
    once (readiness group 2b): four tokens fill the organization's 32, a fifth is
    refused, and another organization's token is not."""
    world = _World()
    ceiling = TOKEN_TIER.org_max_concurrent
    per_token = TOKEN_TIER.max_concurrent
    assert ceiling is not None and per_token is not None
    org_id = str(uuid.uuid4())
    busy = [world.token(org_id) for _ in range(ceiling // per_token)]

    async def slow(token: str) -> httpx.Response:
        return await runner.get(SLOW_READ, headers={"Authorization": f"Bearer {token}"})

    async with _client(world) as runner:
        held = [asyncio.create_task(slow(token)) for token in busy for _ in range(per_token)]
        await asyncio.sleep(0.1)
        elsewhere = asyncio.create_task(slow(world.token(str(uuid.uuid4()))))
        try:
            # A refusal is immediate; a fifth token served instead waits at the
            # gate, so running out of time here is the failure.
            fifth = await asyncio.wait_for(slow(world.token(org_id)), timeout=2)
        finally:
            world.released.set()
        answered = await asyncio.gather(*held)
        other = await elsewhere

    assert fifth.status_code == 429
    assert fifth.json()["detail"] == TOO_MANY_CONCURRENT_DETAIL
    assert {response.status_code for response in answered} == {200}
    assert other.status_code == 200


async def test_an_organizations_in_flight_slots_come_back() -> None:
    """A slot is released when its read finishes, so a ceiling once reached is not
    reached for ever: more reads in a row than the ceiling allows at once."""
    world = _World()
    world.released.set()
    ceiling = TOKEN_TIER.org_max_concurrent
    assert ceiling is not None
    token = world.token(str(uuid.uuid4()))
    async with _client(world) as runner:
        answered = await _reads(runner, token, 2 * ceiling)

    assert set(answered) == {200}
