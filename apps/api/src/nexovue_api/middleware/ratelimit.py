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
from typing import Any, Final

from nexovue_platform.telemetry.logging import get_logger
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexovue_api.auth.clients import client_ip_of
from nexovue_api.auth.sessions import session_token_from_headers, token_fingerprint
from nexovue_api.errors import problems
from nexovue_api.errors.handlers import problem_response
from nexovue_api.ratelimit import headers as ratelimit_headers
from nexovue_api.ratelimit.limiter import Decision, RateLimiter
from nexovue_api.ratelimit.policy import AUTH_TIER, PlanLimits, Tier, tier_for_path

logger = get_logger(__name__)

_HTTP_SCOPE: Final = "http"

# Scopes keep one identity's counters apart from another's.
SCOPE_IP: Final = "ip"
SCOPE_EMAIL: Final = "email"
SCOPE_SESSION: Final = "session"

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
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._plan_limits = plan_limits
        self._enabled = enabled
        self._trusted_proxy_count = trusted_proxy_count

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        tier = self._tier_for(scope)
        limiter, plan_limits = self._dependencies(scope)
        if tier is None or limiter is None or plan_limits is None:
            await self._app(scope, receive, send)
            return

        quota = tier.quota.scaled(await plan_limits.multiplier_for(_org_id(scope)))
        receive, identities = await _identities(receive, tier, scope, self._client_ip(scope))

        decision: Decision | None = None
        for limit_scope, value in identities:
            decision = await limiter.consume(scope=limit_scope, identity=value, quota=quota)
            if not decision.allowed:
                await self._reject(Request(scope, receive), decision, send)
                return

        assert decision is not None
        await self._serve(scope, receive, send, tier, limiter, identities[0], decision)

    async def _serve(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        tier: Tier,
        limiter: RateLimiter,
        identity: Identity,
        decision: Decision,
    ) -> None:
        """Run the request, holding a concurrency slot when the tier caps them."""
        send = _with_limit_headers(send, decision)
        if tier.max_concurrent is None:
            await self._app(scope, receive, send)
            return

        limit_scope, value = identity
        token = await limiter.acquire_slot(
            scope=limit_scope, identity=value, max_concurrent=tier.max_concurrent
        )
        if token is None:
            request = Request(scope, receive)
            await self._reject(request, decision, send, detail=TOO_MANY_CONCURRENT_DETAIL)
            return

        try:
            await self._app(scope, receive, send)
        finally:
            await limiter.release_slot(scope=limit_scope, identity=value, token=token)

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

    def _tier_for(self, scope: Scope) -> Tier | None:
        if not self._enabled or scope["type"] != _HTTP_SCOPE:
            return None
        return tier_for_path(scope["path"])

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
    """A stable id for a signed-in caller, without putting the token in a Redis key."""
    token = session_token_from_headers(Headers(scope=scope))
    # The fingerprint, never the token: a Redis key is not a place for a credential.
    return f"tok:{token_fingerprint(token)}" if token else None


def _org_id(scope: Scope) -> str | None:
    state = scope.get("state") or {}
    org_id = state.get("org_id")
    return str(org_id) if org_id else None
