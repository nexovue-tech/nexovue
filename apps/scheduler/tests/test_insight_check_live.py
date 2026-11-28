"""The connection test, end to end, against a recording site and real Postgres (F3 plan
T4.3; AC34, AC35, AC38, AC53's null-limit case, AC96).

The task runs **as `nexovue_insight`**, through the real `setup` step's grants, so a
statement the account may not run fails here and not in production. The provider is a local
HTTP site that records every byte it is sent, on this machine's private address, listed as
an operator would list a local model (`AI_PRIVATE_ENDPOINTS`). No real provider is called.

Each test makes its own organization and connection, and leaves other rows alone, so it is
safe beside a running dev stack. This suite fails rather than skips when Postgres is down.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from insight_login import DEV_INSIGHT_PASSWORD, insight_dsn
from live import require_stack
from nexovue_platform.ai.check_prompt import CHECK_REQUEST
from nexovue_platform.ai.connection import AI_CONNECTION_PURPOSE, check_fingerprint, key_owner
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.jobs import CHECK_AI_CONNECTION_TASK
from nexovue_platform.checks.sealing import (
    ConfiguredKeys,
    Keyring,
    SealedBox,
    decode_key,
    generate_key,
)
from recording_site import RecordingSite, Reply, json_reply, serve_recording_site
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api import insight_account
from nexovue_api.signup.service import provision_tenant
from nexovue_scheduler.insight import check, store
from nexovue_scheduler.insight.transport import AI_USER_AGENT
from nexovue_scheduler.jobs import registry
from nexovue_scheduler.probes.addresses import PrivateEndpoints

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
INSIGHT_ASYNC_DSN: Final = insight_dsn(DSN).replace("postgresql://", "postgresql+asyncpg://", 1)
KEY_RING: Final = generate_key()
KEYS: Final = ConfiguredKeys(KEY_RING)
PROVIDER_KEY: Final = "sk-test-check-key"
# A pinned clock: nothing here depends on the day it runs.
NOW: Final = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
NOVEMBER: Final = date(2026, 11, 1)
CHAT_PATH: Final = "/v1/chat/completions"


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/scheduler/tests/test_insight_check_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


def _openai_ok(model: str = "llama3.1:8b") -> Reply:
    return json_reply(
        {
            "model": model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"ok":true}'},
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 6},
        }
    )


@pytest.fixture
def site() -> Iterator[RecordingSite]:
    with serve_recording_site() as served:
        yield served


@pytest.fixture
async def install(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    monkeypatch.setenv(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@pytest.fixture
async def as_insight(install: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(INSIGHT_ASYNC_DSN, pool_size=1, max_overflow=0)
    yield built
    await built.dispose()


@dataclass(frozen=True)
class Org:
    org_id: str
    user_id: str
    connection_id: str
    base_url: str


async def _organization(install: AsyncEngine) -> tuple[str, str]:
    async with AsyncSession(install, expire_on_commit=False) as session, session.begin():
        made = await provision_tenant(
            session,
            organization_name="AI check suite",
            email=f"ai-check-{uuid.uuid4().hex[:10]}@example.test",
            password="correct horse battery staple",
        )
    return str(made.organization.id), str(made.user.id)


async def _connect(
    install: AsyncEngine,
    base_url: str,
    *,
    key: str | None = PROVIDER_KEY,
    sealed_for: str | None = None,
    limit: int | None = None,
) -> Org:
    org_id, user_id = await _organization(install)
    connection_id = str(uuid.uuid4())
    sealed = None
    if key is not None:
        box = SealedBox(Keyring(current=decode_key(KEY_RING)), purpose=AI_CONNECTION_PURPOSE)
        sealed = box.seal(key, owner=key_owner(org_id, connection_id, sealed_for or base_url))
    async with install.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO ai_connections (id, org_id, wire_format, base_url, model, "
                "key_id, sealed_key, monthly_token_limit) VALUES (:id, :org, 'openai_chat', "
                ":url, 'llama3.1:8b', :key_id, :sealed, :limit)"
            ),
            {
                "id": connection_id,
                "org": org_id,
                "url": base_url,
                "key_id": None if key is None else str(uuid.uuid4()),
                "sealed": sealed,
                "limit": limit,
            },
        )
    return Org(org_id, user_id, connection_id, base_url)


@pytest.fixture
async def cleanup(install: AsyncEngine) -> AsyncIterator[list[str]]:
    made: list[str] = []
    yield made
    async with install.begin() as connection:
        for org_id in made:
            await connection.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": org_id}
            )


def _endpoints(site: RecordingSite) -> PrivateEndpoints:
    return PrivateEndpoints.parse(f"{site.host}:{site.port}")


async def _check(
    engine: AsyncEngine, org: Org, site: RecordingSite, *, endpoints: PrivateEndpoints | None = None
) -> tuple[check.Tested, str]:
    request_id = str(uuid.uuid4())
    tested = await check.check(
        engine,
        org_id=org.org_id,
        request_id=request_id,
        requested_by=org.user_id,
        keys=KEYS,
        endpoints=_endpoints(site) if endpoints is None else endpoints,
        clock=lambda: NOW,
    )
    return tested, request_id


async def _one(install: AsyncEngine, sql: str, **bound: Any) -> Any:
    async with install.connect() as connection:
        return (await connection.execute(text(sql), bound)).one_or_none()


# --------------------------------------------------------------------- passing


async def test_a_passing_test_sends_the_constant_body_and_is_recorded_and_metered(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """AC35: the body holds no finding data; AC53: a test with no limit set runs, is recorded
    and metered, and reserves nothing."""
    org = await _connect(install, site.url("/v1"))
    cleanup.append(org.org_id)
    site.replies[CHAT_PATH] = _openai_ok()

    tested, request_id = await _check(as_insight, org, site)

    assert tested.ok is True and tested.reason is None
    [sent] = site.requests
    expected = wire_format("openai_chat").build(
        CHECK_REQUEST, model="llama3.1:8b", base_url=org.base_url, key=None
    )
    assert sent.path == CHAT_PATH
    assert sent.body == expected.body
    assert org.org_id.encode() not in sent.body
    assert sent.headers["authorization"] == f"Bearer {PROVIDER_KEY}"
    assert sent.headers["user-agent"] == AI_USER_AGENT
    run = await _one(
        install,
        "SELECT purpose, outcome, input_tokens, output_tokens, tokens_estimated, reserved_tokens, "
        "provider_host, request_sha256, requested_by FROM ai_runs WHERE org_id = :org",
        org=org.org_id,
    )
    assert (run.purpose, run.outcome, run.input_tokens, run.output_tokens) == (
        "check",
        "done",
        40,
        6,
    )
    assert run.tokens_estimated is False and run.reserved_tokens == 0
    assert run.provider_host == f"http://{site.host}:{site.port}"
    assert run.request_sha256 == expected.sha256()
    assert str(run.requested_by) == org.user_id
    usage = await _one(
        install,
        "SELECT used_tokens, reserved_tokens FROM ai_usage_monthly WHERE org_id = :org "
        "AND month = :month",
        org=org.org_id,
        month=NOVEMBER,
    )
    assert (usage.used_tokens, usage.reserved_tokens) == (46, 0)
    connection = await _one(
        install,
        "SELECT checked_ok, checked_fingerprint, key_id, checked_at FROM ai_connections "
        "WHERE org_id = :org",
        org=org.org_id,
    )
    assert connection.checked_ok is True and connection.checked_at == NOW
    assert connection.checked_fingerprint == check_fingerprint(
        wire_format="openai_chat",
        base_url=org.base_url,
        model="llama3.1:8b",
        region=None,
        key_id=str(connection.key_id),
    )
    result = await _one(
        install,
        "SELECT org_id, kind, payload FROM job_results WHERE request_id = CAST(:id AS uuid)",
        id=request_id,
    )
    assert (str(result.org_id), result.kind) == (org.org_id, "ai_check")
    assert result.payload["ok"] is True and result.payload["input_tokens"] == 40


# ---------------------------------------------------------- what must not happen


async def test_a_connection_with_no_key_sends_no_authorization_header(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """AC34: after a URL change with no new key the key is gone, and the test sends none:
    the recording site receives no `Authorization` header, and the test fails."""
    org = await _connect(install, site.url("/v1"), key=None)
    cleanup.append(org.org_id)
    site.replies[CHAT_PATH] = json_reply({"error": {"code": "invalid_api_key"}}, status=401)

    tested, _ = await _check(as_insight, org, site)

    assert (tested.ok, tested.reason) == (False, "bad_key")
    [sent] = site.requests
    assert "authorization" not in sent.headers
    checked = await _one(
        install, "SELECT checked_ok FROM ai_connections WHERE org_id = :o", o=org.org_id
    )
    assert checked.checked_ok is False


async def test_a_key_sealed_for_another_address_does_not_open_and_nothing_is_sent(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """AC34: a key sealed for one address must not open for another."""
    org = await _connect(install, site.url("/v1"), sealed_for="https://api.openai.com/v1")
    cleanup.append(org.org_id)
    site.replies[CHAT_PATH] = _openai_ok()

    tested, _ = await _check(as_insight, org, site)

    assert (tested.ok, tested.reason) == (False, "key_unreadable")
    assert site.requests == []
    assert await _one(install, "SELECT id FROM ai_runs WHERE org_id = :o", o=org.org_id) is None


async def test_a_redirect_is_never_followed(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """AC38: a provider answering 302 to a second path gets zero requests there."""
    org = await _connect(install, site.url("/v1"))
    cleanup.append(org.org_id)
    site.replies[CHAT_PATH] = Reply(status=302, headers={"Location": site.url("/elsewhere")})
    site.replies["/elsewhere"] = _openai_ok()

    tested, _ = await _check(as_insight, org, site)

    assert tested.ok is False
    assert [sent.path for sent in site.requests] == [CHAT_PATH]


async def test_an_unlisted_private_address_is_refused_at_connect(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """FR32, AC37: an address the operator did not list is not contacted, and says so."""
    org = await _connect(install, site.url("/v1"))
    cleanup.append(org.org_id)
    site.replies[CHAT_PATH] = _openai_ok()

    tested, _ = await _check(as_insight, org, site, endpoints=PrivateEndpoints())

    assert (tested.ok, tested.reason) == (False, "address_refused")
    assert site.requests == []
    run = await _one(
        install, "SELECT outcome, input_tokens FROM ai_runs WHERE org_id = :o", o=org.org_id
    )
    assert (run.outcome, run.input_tokens) == ("unavailable", None), "never reached: not billed"


async def test_a_stored_address_that_breaks_the_rules_is_refused_at_call_time(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """AC34: a row written past the API (a query, plain http to a name) is checked again."""
    org = await _connect(install, f"http://{site.host}:{site.port}/v1?token=1")
    cleanup.append(org.org_id)

    tested, _ = await _check(as_insight, org, site)

    assert (tested.ok, tested.reason) == (False, "not_allowed")
    assert site.requests == []


async def test_a_hostile_model_name_never_reaches_the_result_or_the_run(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """AC96: a provider's reported `model` is outside text."""
    org = await _connect(install, site.url("/v1"))
    cleanup.append(org.org_id)
    site.replies[CHAT_PATH] = _openai_ok(model="<img src=x> CANARY-7f3")

    tested, request_id = await _check(as_insight, org, site)

    assert tested.ok is True and tested.model_reported is None
    rows = await _one(
        install,
        "SELECT (SELECT payload::text FROM job_results WHERE request_id = CAST(:id AS uuid)) "
        "AS payload, (SELECT row_to_json(r)::text FROM ai_runs r WHERE org_id = :o) AS run",
        id=request_id,
        o=org.org_id,
    )
    assert "CANARY" not in rows.payload and "CANARY" not in rows.run


async def test_another_organization_s_job_touches_nothing_here(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """Each statement binds the job's own organization: one with no connection finds none."""
    org = await _connect(install, site.url("/v1"))
    other_org, other_user = await _organization(install)
    cleanup.extend([org.org_id, other_org])
    site.replies[CHAT_PATH] = _openai_ok()

    stranger = Org(other_org, other_user, org.connection_id, org.base_url)
    tested, _ = await _check(as_insight, stranger, site)

    assert (tested.ok, tested.reason) == (False, "no_connection")
    assert site.requests == []
    checked = await _one(
        install, "SELECT checked_at FROM ai_connections WHERE org_id = :o", o=org.org_id
    )
    assert checked.checked_at is None


async def test_a_result_lands_only_on_the_values_it_tested(
    install: AsyncEngine, as_insight: AsyncEngine, site: RecordingSite, cleanup: list[str]
) -> None:
    """A model saved while a test ran keeps its own, untested, state (design)."""
    org = await _connect(install, site.url("/v1"))
    cleanup.append(org.org_id)
    tested_values = await store.load_connection(as_insight, org.org_id)
    assert tested_values is not None
    async with install.begin() as connection:
        await connection.execute(
            text("UPDATE ai_connections SET model = 'other-model' WHERE org_id = :o"),
            {"o": org.org_id},
        )

    await store.finish_check(
        as_insight,
        org_id=org.org_id,
        connection=tested_values,
        fingerprint="f" * 64,
        passed=True,
        at=NOW,
        run=None,
    )

    checked = await _one(
        install, "SELECT checked_ok FROM ai_connections WHERE org_id = :o", o=org.org_id
    )
    assert checked.checked_ok is None


# ------------------------------------------------------------------ the task


def test_the_task_is_the_ai_role_s_alone() -> None:
    """A task runs only where its keys are: the insight set loads it, `all` never does."""
    assert "nexovue_scheduler.insight.check" in registry.TASK_SETS["insight"]
    assert not any(
        module.startswith("nexovue_scheduler.insight") for module in registry.TASK_SETS["all"]
    )
    assert CHECK_AI_CONNECTION_TASK in registry.registered(registry.TASK_SETS["insight"])


def test_the_job_carries_ids_only() -> None:
    from nexovue_platform.ai.jobs import check_request

    job = check_request(org_id="o", request_id="r", requested_by=None)
    assert job.queue == "insight"
    assert json.loads(json.dumps(job.kwargs)) == {
        "org_id": "o",
        "request_id": "r",
        "requested_by": None,
    }
