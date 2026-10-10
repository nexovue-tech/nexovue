"""Tiered rate limiting, applied before a request costs anything.

Ours rather than slowapi or fastapi-limiter: neither can take a limit from the
organization's plan in Postgres, and neither caps concurrency, which is the
control that actually protects a columnar store.

It is a middleware and not a dependency because a dependency runs after routing
and after the body has been parsed — by then the request has already cost what
the limit exists to avoid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.telemetry.logging import get_logger
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexovue_api.auth.clients import client_ip_of
from nexovue_api.auth.sessions import (
    Session,
    SessionStore,
    bearer_token_from_headers,
    session_token_from_headers,
    token_fingerprint,
)
from nexovue_api.errors import problems
from nexovue_api.errors.handlers import problem_response
from nexovue_api.ratelimit import headers as ratelimit_headers
from nexovue_api.ratelimit.limiter import Decision, RateLimiter
from nexovue_api.ratelimit.policy import (
    AUTH_TIER,
    TOKEN_TIER,
    PlanLimits,
    Quota,
    Tier,
    tier_for,
)
from nexovue_api.ratelimit.token_owners import TokenOwners

logger = get_logger(__name__)

_HTTP_SCOPE: Final = "http"

# Scopes keep one identity's counters apart from another's.
SCOPE_IP: Final = "ip"
SCOPE_EMAIL: Final = "email"
SCOPE_SESSION: Final = "session"
SCOPE_ORGANIZATION: Final = "organization"
SCOPE_TOKEN: Final = "token"

# An auth body is a handful of fields. Anything larger is not a login attempt,
# and buffering it to read the email would be the attack rather than the defence.
MAX_AUTH_BODY_BYTES: Final = 8 * 1024

UNKNOWN_CLIENT: Final = "unknown"
FORWARDED_FOR_HEADER: Final = "x-forwarded-for"

TOO_MANY_REQUESTS_DETAIL: Final = "Too many requests. Retry after the interval in Retry-After."
TOO_MANY_CONCURRENT_DETAIL: Final = (
    "Too many queries in flight for this caller. Retry when one finishes."
)

Identity = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Counted:
    """One bucket a request is counted in: whose, how many, and how many at once.

    No quota is a bucket that only caps what is in flight.
    """

    identity: Identity
    quota: Quota | None
    max_concurrent: int | None


class RateLimitMiddleware:
    """Hold every request to the tier its path falls into."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter | None = None,
        plan_limits: PlanLimits | None = None,
        enabled: bool = True,
        trusted_proxy_count: int = 0,
        sessions: SessionStore | None = None,
        token_owners: TokenOwners | None = None,
    ) -> None:
        self._app = app
        self._sessions = sessions
        self._token_owners = token_owners
        self._limiter = limiter
        self._plan_limits = plan_limits
        self._enabled = enabled
        self._trusted_proxy_count = trusted_proxy_count

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        presented = self._presented(scope)
        tier = self._tier_for(scope, presented)
        limiter, plan_limits = self._dependencies(scope)
        if tier is None or limiter is None or plan_limits is None:
            await self._app(scope, receive, send)
            return

        if tier is TOKEN_TIER and presented is not None:
            # A machine credential pays for itself: never the address it dialled
            # from (one CI runner is many tokens) and never a person (there is
            # none) — but its organization's ceiling holds however many tokens
            # that organization mints.
            owner = await self._organization_of(scope, presented)
            multiplier = await plan_limits.multiplier_for(owner)
            await self._limit(
                scope, receive, send, limiter, _per_token(tier, presented, owner, multiplier)
            )
            return

        signed_in = None if tier is AUTH_TIER else await self._signed_in(scope)
        multiplier = await plan_limits.multiplier_for(signed_in.org_id if signed_in else None)
        if signed_in is not None:
            counted = _per_person(tier, signed_in, multiplier)
        else:
            receive, identities = await _identities(receive, tier, scope, self._client_ip(scope))
            counted = _per_address(tier, identities, multiplier)

        await self._limit(scope, receive, send, limiter, counted)

    async def _limit(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        limiter: RateLimiter,
        counted: list[Counted],
    ) -> None:
        """Spend every bucket this request falls in, then serve it."""
        decision: Decision | None = None
        for bucket in counted:
            if bucket.quota is None:
                continue
            limit_scope, value = bucket.identity
            outcome = await limiter.consume(scope=limit_scope, identity=value, quota=bucket.quota)
            if not outcome.allowed:
                await self._reject(Request(scope, receive), outcome, send)
                return
            # The first bucket is the caller's own; its state is what the headers report.
            decision = decision or outcome

        assert decision is not None
        await self._serve(scope, receive, send, limiter, counted, decision)

    async def _serve(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        limiter: RateLimiter,
        counted: list[Counted],
        decision: Decision,
    ) -> None:
        """Run the request, holding a concurrency slot in every bucket that caps them."""
        send = _with_limit_headers(send, decision)
        held: list[Identity] = []
        try:
            for bucket in counted:
                if bucket.max_concurrent is None:
                    continue
                limit_scope, value = bucket.identity
                granted = await limiter.acquire_slot(
                    scope=limit_scope, identity=value, max_concurrent=bucket.max_concurrent
                )
                if not granted:
                    request = Request(scope, receive)
                    await self._reject(request, decision, send, detail=TOO_MANY_CONCURRENT_DETAIL)
                    return
                held.append(bucket.identity)
            await self._app(scope, receive, send)
        finally:
            for limit_scope, value in held:
                await limiter.release_slot(scope=limit_scope, identity=value)

    async def _signed_in(self, scope: Scope) -> Session | None:
        """The live session a request carries, or None — read without touching it.

        Only a token that names a live session earns per-person counting: an
        invented one resolves to nothing and is counted against its address, so
        it buys a caller nothing.
        """
        sessions = self._sessions or getattr(
            getattr(getattr(scope.get("app"), "state", None), "resources", None), "sessions", None
        )
        token = session_token_from_headers(Headers(scope=scope))
        if sessions is None or not token:
            return None
        return await sessions.peek(token)

    async def _organization_of(self, scope: Scope, presented: str) -> str | None:
        owners = self._token_owners or getattr(
            getattr(scope.get("app"), "state", None), "token_owners", None
        )
        return None if owners is None else await owners.organization_of(presented)

    async def _reject(
        self,
        request: Request,
        decision: Decision,
        send: Send,
        detail: str = TOO_MANY_REQUESTS_DETAIL,
    ) -> None:
        logger.warning(
            "rate limit rejected a request",
            path=request.url.path,
            policy=decision.quota.name,
            retry_after_seconds=decision.retry_after_seconds,
        )
        response = problem_response(
            request,
            problems.RATE_LIMIT_EXCEEDED,
            detail,
            extensions={"violated-policies": [decision.quota.name]},
            headers=ratelimit_headers.rejection_headers(decision),
        )
        await response(request.scope, request.receive, send)

    def _tier_for(self, scope: Scope, presented: str | None) -> Tier | None:
        if not self._enabled or scope["type"] != _HTTP_SCOPE:
            return None
        return tier_for(scope["path"], presented=presented)

    def _presented(self, scope: Scope) -> str | None:
        """The bearer value, whatever kind of credential it is."""
        if scope["type"] != _HTTP_SCOPE:
            return None
        return bearer_token_from_headers(Headers(scope=scope))

    def _dependencies(self, scope: Scope) -> tuple[RateLimiter | None, PlanLimits | None]:
        """Explicit wiring wins; otherwise take what the lifespan put on the app."""
        if self._limiter is not None and self._plan_limits is not None:
            return self._limiter, self._plan_limits
        state = getattr(scope.get("app"), "state", None)
        return getattr(state, "rate_limiter", None), getattr(state, "plan_limits", None)

    def _client_ip(self, scope: Scope) -> str:
        """The caller's address, from the one place that decides what to trust.

        This held a second copy of the rule until 2026-09-14. **Two copies of a
        trust boundary is one too many**: whichever drifts first does so
        silently, and the two failures are a rate limiter counting the proxy
        instead of the caller, and a session list showing somebody a network
        that is not theirs. `auth.clients.client_ip_of` is the rule; this reads
        the setting for itself only because middleware runs before any
        dependency exists to hand it one.
        """
        client = scope.get("client")
        return (
            client_ip_of(
                Headers(scope=scope),
                direct_peer=str(client[0]) if client else None,
                trusted_proxy_count=self._trusted_proxy_count,
            )
            or UNKNOWN_CLIENT
        )


def _per_person(tier: Tier, session: Session, multiplier: float) -> list[Counted]:
    """A signed-in read: counted for the person, then for their organization.

    Not the address: a team in one office is several people behind one address,
    and counting them together gave five colleagues one person's budget
    (`docs/changes/rate-limit-per-person/`).
    """
    counted = [
        Counted(
            identity=(SCOPE_SESSION, f"user:{session.user_id}"),
            quota=tier.quota.scaled(multiplier),
            max_concurrent=tier.max_concurrent,
        )
    ]
    if tier.org_quota is not None:
        counted.append(
            Counted(
                identity=(SCOPE_ORGANIZATION, session.org_id),
                quota=tier.org_quota.scaled(multiplier),
                max_concurrent=tier.org_max_concurrent,
            )
        )
    return counted


def _per_token(tier: Tier, presented: str, owner: str | None, multiplier: float) -> list[Counted]:
    """The token's own bucket, keyed by its fingerprint, then its organization's.

    The fingerprint and never the token: a counter key is not a place for a
    credential, which is the rule the signed-out path already follows. A token
    that is not ours has no organization, and authentication refuses it next.
    """
    counted = [
        Counted(
            identity=(SCOPE_TOKEN, f"tok:{token_fingerprint(presented)}"),
            quota=tier.quota.scaled(multiplier),
            max_concurrent=tier.max_concurrent,
        )
    ]
    if owner is not None and (tier.org_quota is not None or tier.org_max_concurrent is not None):
        counted.append(
            Counted(
                identity=(SCOPE_ORGANIZATION, owner),
                quota=None if tier.org_quota is None else tier.org_quota.scaled(multiplier),
                max_concurrent=tier.org_max_concurrent,
            )
        )
    return counted


def _per_address(tier: Tier, identities: list[Identity], multiplier: float) -> list[Counted]:
    """Nobody signed in, or a sign-in route: every identity on the tier's quota,
    with the concurrency cap on the address."""
    quota = tier.quota.scaled(multiplier)
    return [
        Counted(
            identity=identity,
            quota=quota,
            max_concurrent=tier.max_concurrent if index == 0 else None,
        )
        for index, identity in enumerate(identities)
    ]


def _with_limit_headers(send: Send, decision: Decision) -> Send:
    """Advertise the quota on the successful response too, so clients can pace."""

    async def send_with_headers(message: Message) -> None:
        if message["type"] == "http.response.start":
            response_headers = MutableHeaders(scope=message)
            for name, value in ratelimit_headers.limit_headers(decision).items():
                response_headers[name] = value
        await send(message)

    return send_with_headers


async def _identities(
    receive: Receive, tier: Tier, scope: Scope, client_ip: str
) -> tuple[Receive, list[Identity]]:
    """Who this request is counted against — and a receive that can still be read.

    Auth requests are counted per IP *and* per email: neither a rotating address
    nor a rotating account should get a clean run at a single target.
    """
    identities: list[Identity] = [(SCOPE_IP, client_ip)]
    session = _session_identity(scope)
    if session is not None:
        identities.append((SCOPE_SESSION, session))

    if tier is not AUTH_TIER:
        return receive, identities

    body, replayable = await _buffered_body(receive)
    email = _email_from(body)
    if email:
        identities.append((SCOPE_EMAIL, email))
    return replayable, identities


async def _buffered_body(receive: Receive) -> tuple[bytes, Receive]:
    """Read the body, then hand back a receive that replays it to the endpoint."""
    chunks: list[bytes] = []
    buffered = 0
    more = True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk: bytes = message.get("body", b"")
        buffered += len(chunk)
        if buffered <= MAX_AUTH_BODY_BYTES:
            chunks.append(chunk)
        more = bool(message.get("more_body", False))

    body = b"".join(chunks)
    replayed = False

    async def replay() -> Message:
        nonlocal replayed
        if replayed:
            return {"type": "http.disconnect"}
        replayed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return body, replay


def _email_from(body: bytes) -> str | None:
    """The email in an auth body, lowercased. Malformed input is simply not an id."""
    if not body:
        return None
    try:
        payload: Any = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    email = payload.get("email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def _session_identity(scope: Scope) -> str | None:
    """A stable id for a signed-in caller, without putting the token in a counter key."""
    token = session_token_from_headers(Headers(scope=scope))
    # The fingerprint, never the token: a counter key is not a place for a credential.
    return f"tok:{token_fingerprint(token)}" if token else None
