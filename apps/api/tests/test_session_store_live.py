"""What a session keeps when it is used (real Redis).

A session is rewritten every time it is seen, to record "last used". Anything
that rewrite forgets is silently dropped from a live session — which is how a
read-only support view became an ordinary one after sixty seconds
(`docs/changes/super-admin-in-the-console/`, found 2026-09-20).

So this holds the rewrite to the whole record: every field a session carries
must survive being used, and each is named here rather than counted, because
the next field added is the next one that can be forgotten.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from redis.asyncio import Redis

from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.sessions import (
    LAST_SEEN_RESOLUTION_SECONDS,
    Impersonation,
    SessionStore,
    _key,  # the store's own key shape
    token_fingerprint,
)

REDIS_URL: Final = os.environ.get("NEXOVUE_REDIS_URL", "redis://localhost:16379/0")


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 6379), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(REDIS_URL),
    suite="apps/api/tests/test_session_store_live.py",
    remedy="Redis is not reachable — run `just up`",
)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(REDIS_URL)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def store(redis: Redis) -> SessionStore:
    return SessionStore(redis, ttl_seconds=600)


def _support_view() -> Impersonation:
    return Impersonation(
        impersonation_id=str(uuid.uuid4()),
        operator_label="Raj at nexovue",
        reason="checkout shows no traces",
        grant_id=str(uuid.uuid4()),
        until=datetime.now(UTC) + timedelta(minutes=30),
    )


async def _used_again(store: SessionStore, redis: Redis, token: str) -> None:
    """Age the stored record past the last-seen resolution, then read it.

    Ageing it in Redis rather than waiting a minute: what is under test is the
    rewrite, and the rewrite only happens once a session looks stale.
    """
    key = _key(token_fingerprint(token))
    stored = json.loads(await redis.get(key))
    aged = datetime.now(UTC) - timedelta(seconds=LAST_SEEN_RESOLUTION_SECONDS + 1)
    stored["last_seen_at"] = aged.isoformat()
    await redis.set(key, json.dumps(stored), keepttl=True)
    await store.get(token)


async def test_a_support_view_is_still_a_support_view_after_it_is_used(
    store: SessionStore, redis: Redis
) -> None:
    """The defect: a read-only view became an ordinary session, as the customer,
    sixty seconds in — every write refused before that, and none after."""
    impersonation = _support_view()
    token, _ = await store.create(
        user_id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), impersonation=impersonation
    )

    await _used_again(store, redis, token)

    session = await store.peek(token)
    assert session is not None
    assert session.impersonation is not None
    assert session.impersonation.impersonation_id == impersonation.impersonation_id
    assert session.impersonation.until == impersonation.until


async def test_what_a_session_is_recognised_by_survives_being_used(
    store: SessionStore, redis: Redis
) -> None:
    client = ClientDescription(label="Chrome on macOS", network="203.0.113.0/24")
    token, _ = await store.create(
        user_id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), client=client
    )

    await _used_again(store, redis, token)

    session = await store.peek(token)
    assert session is not None
    assert session.client == client
    assert session.last_seen_at is not None
