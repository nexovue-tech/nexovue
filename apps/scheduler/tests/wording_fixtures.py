"""What the wording suites share (F3 plan T5.3 to T5.6): an install with the AI account's real
grants, an organization with a consented, switched-on connection to a fake provider, and
versions waiting for words.

The fake provider is `recording_site`: a local HTTP site on this machine's private address,
listed as an operator lists a local model (`AI_PRIVATE_ENDPOINTS`), that records every byte
it is sent. **No real provider is ever called.** Every `word` and `sweep` here runs as
`nexovue_insight`, so a statement its grants do not allow fails here and not in production.

The clock is pinned once per run, close to the database's, so the stack's own AI sweep, which
reads the real clock, never takes these rows before a test does.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from alert_fixtures import POSTGRES_DSN, AlertTenant, add_finding, drop_tenant, make_tenant
from insight_login import DEV_INSIGHT_PASSWORD, insight_dsn
from nexovue_platform.ai.connection import AI_CONNECTION_PURPOSE, consent_fingerprint, key_owner
from nexovue_platform.ai.explain_prompt import SAMPLE_MODEL_INPUT
from nexovue_platform.checks.sealing import (
    ConfiguredKeys,
    Keyring,
    SealedBox,
    decode_key,
    generate_key,
)
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from recording_site import RecordingSite, Reply, json_reply, serve_recording_site
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_api import insight_account
from nexovue_scheduler.insight import transport, word
from nexovue_scheduler.probes.addresses import PrivateEndpoints

ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
INSIGHT_ASYNC_DSN: Final = insight_dsn(ASYNC_DSN)
KEY_RING: Final = generate_key()
KEYS: Final = ConfiguredKeys(KEY_RING)
PROVIDER_KEY: Final = "sk-test-wording-key"
MODEL: Final = "llama3.1:8b"
CHAT_PATH: Final = "/v1/chat/completions"
NOW: Final = datetime.now(UTC).replace(microsecond=0)
GOOD: Final = "service_1 was deployed as version_1 4 minutes before it slowed."
# The sample input's names, as the gather would have stored them apart from it.
ALIASES: Final = {
    "names": {"service_1": "checkout", "service_2": "orders-db", "version_1": "v2.1"},
    "facts": {"e1": "deploy:d-1", "e2": "dependency:orders-db", "e3": "absence:no_host_names"},
}


def answer(
    *claims: dict[str, Any], picks: list[str] | None = None, abstain: bool = False
) -> dict[str, Any]:
    return {
        "claims": list(claims) or [{"type": "timing", "text": GOOD, "evidence_ids": ["e1"]}],
        "next_checks": picks if picks is not None else ["c2"],
        "abstain": abstain,
    }


def openai_reply(
    content: str | dict[str, Any],
    *,
    prompt_tokens: int = 400,
    completion_tokens: int = 60,
    model: str = MODEL,
    finish: str = "stop",
    refusal: str | None = None,
    delay: float = 0.0,
) -> Reply:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) else json.dumps(content),
    }
    if refusal is not None:
        message = {"role": "assistant", "content": None, "refusal": refusal}
    reply = json_reply(
        {
            "model": model,
            "choices": [{"finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
    )
    reply.delay = delay
    return reply


@pytest.fixture
def provider_site() -> Iterator[RecordingSite]:
    with serve_recording_site() as served:
        yield served


@pytest.fixture
async def ai_install(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    monkeypatch.setenv(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN, pool_size=4)
    yield built
    await built.dispose()


@pytest.fixture
async def ai_engine(ai_install: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(INSIGHT_ASYNC_DSN, pool_size=6, max_overflow=4)
    yield built
    await built.dispose()


@pytest.fixture
async def ai_tenant(ai_install: AsyncEngine) -> AsyncIterator[AlertTenant]:
    made = await make_tenant(ai_install, with_check=False)
    try:
        yield made
    finally:
        await drop_tenant(ai_install, made)


@dataclass(frozen=True)
class Connected:
    tenant: AlertTenant
    connection_id: str
    base_url: str
    endpoints: PrivateEndpoints


async def connect(
    install: AsyncEngine,
    tenant: AlertTenant,
    site: RecordingSite,
    *,
    limit: int = 1_000_000,
    enabled: bool = True,
    sealed_for: str | None = None,
    consented: bool = True,
) -> Connected:
    """A connection as an owner leaves it after the whole flow: tested, limited, consented,
    on (BR4). `sealed_for` seals the key for another address, as a copied row would be."""
    org_id = str(tenant.org_id)
    connection_id = str(uuid.uuid4())
    base_url = site.url("/v1")
    box = SealedBox(Keyring(current=decode_key(KEY_RING)), purpose=AI_CONNECTION_PURPOSE)
    key_id = str(uuid.uuid4())
    sealed = box.seal(PROVIDER_KEY, owner=key_owner(org_id, connection_id, sealed_for or base_url))
    fingerprint = consent_fingerprint(
        wire_format="openai_chat", base_url=base_url, region=None, key_id=key_id
    )
    async with install.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO ai_connections (
                    id, org_id, wire_format, base_url, model, key_id, sealed_key, call_seconds,
                    monthly_token_limit, enabled, consented_at, consented_base_url,
                    consented_fingerprint, consented_schema
                ) VALUES (
                    :id, :org, 'openai_chat', :url, :model, :key_id, :sealed, 30, :limit,
                    :enabled, :at, :url, :fingerprint, :schema
                )
                """
            ),
            {
                "id": connection_id,
                "org": org_id,
                "url": base_url,
                "model": MODEL,
                "key_id": key_id,
                "sealed": sealed,
                "limit": limit,
                "enabled": enabled,
                "at": NOW,
                "fingerprint": fingerprint if consented else "0" * 64,
                "schema": SCHEMA_VERSION,
            },
        )
    return Connected(
        tenant, connection_id, base_url, PrivateEndpoints.parse(f"{site.host}:{site.port}")
    )


async def finding(
    install: AsyncEngine, tenant: AlertTenant, *, severity: str = "warning", **changes: Any
) -> uuid.UUID:
    """An open finding on a service of its own: a service holds one open finding at a time."""
    changes.setdefault("service", f"service-{uuid.uuid4().hex[:8]}")
    async with install.begin() as connection:
        made: uuid.UUID = await add_finding(
            connection, tenant, severity=severity, started_at=NOW - timedelta(minutes=20), **changes
        )
        return made


async def version(
    install: AsyncEngine,
    tenant: AlertTenant,
    finding_id: uuid.UUID,
    *,
    number: int = 1,
    model_input: dict[str, Any] | None = None,
    state: str = "pending",
    generation: int = 1,
    changed_at: datetime = NOW,
    severity: str = "warning",
) -> str:
    """A version as gather leaves one waiting for words."""
    sent = dict(model_input or SAMPLE_MODEL_INPUT)
    sent["finding"] = dict(sent["finding"], severity=severity)
    async with install.begin() as connection:
        made = await connection.scalar(
            text(
                """
                INSERT INTO finding_explanations (
                    org_id, project_id, finding_id, version, read_at, fingerprint, evidence,
                    aliases, summary, model_input, wording_state, wording_generation,
                    wording_changed_at
                ) VALUES (
                    :org, :project, :finding, :number, :at, :fingerprint, '{"items": []}',
                    CAST(:aliases AS jsonb), '{}', CAST(:input AS jsonb), :state, :generation,
                    :changed_at
                )
                RETURNING id
                """
            ),
            {
                "org": tenant.org_id,
                "project": tenant.project_id,
                "finding": finding_id,
                "number": number,
                "at": NOW,
                "fingerprint": f"{number:064d}",
                "aliases": json.dumps(ALIASES),
                "input": json.dumps(sent),
                "state": state,
                "generation": generation,
                "changed_at": changed_at,
            },
        )
    return str(made)


async def run_word(
    as_insight: AsyncEngine,
    connected: Connected,
    explanation_id: str,
    *,
    generation: int = 1,
    attempt: int = 1,
    at: datetime = NOW,
    send: transport.Send = transport.send,
    requested_by: str | None = None,
) -> str:
    return await word.word(
        as_insight,
        explanation_id=explanation_id,
        org_id=str(connected.tenant.org_id),
        generation=generation,
        requested_by=requested_by,
        keys=KEYS,
        endpoints=connected.endpoints,
        deployment="self-hosted",
        clock=lambda: at,
        send=send,
        attempt=attempt,
    )


async def one(install: AsyncEngine, sql: str, **bound: Any) -> Any:
    async with install.connect() as connection:
        return (await connection.execute(text(sql), bound)).one_or_none()


async def every(install: AsyncEngine, sql: str, **bound: Any) -> list[Any]:
    async with install.connect() as connection:
        return list(await connection.execute(text(sql), bound))


async def wording_of(install: AsyncEngine, explanation_id: str) -> Any:
    return await one(
        install,
        "SELECT wording, wording_state, wording_reason, wording_from_version, "
        "wording_generation FROM finding_explanations WHERE id = :id",
        id=explanation_id,
    )
