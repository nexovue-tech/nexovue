"""The LLM-calls read against the real ClickHouse.

The offline suite proves the statement is scoped and the absence-versus-zero
rule is folded correctly. It cannot tell you `JSONAllPaths` parses against this
ClickHouse version, that `getSubcolumn` reads a real `gen_ai.*` path, or that
`tools/gateseed`'s fixtures actually land the three shapes this feature was
built to tell apart. This one asks the store.

It fails when the stack is not up rather than skipping, because a skip here is
indistinguishable from a pass. `NEXOVUE_SKIP_LIVE_TESTS=1` opts out on purpose.

Reads `gateseed`'s own fixtures (`tools/gateseed/payload.go`'s `GenAISpans`,
committed in `ba9311a`) rather than seeding anything new — they already cover
exactly the three cases this suite asserts: a complete call, an unlabeled-model
call, and a call whose model-name field is under a name this feature does not
read, standing in for the convention's next rename (AC29.6).
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest_asyncio
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.llm_observability import service

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_HTTP_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
GATESEED_SERVICE_NAME: Final = "gateseed"


def _reachable() -> bool:
    parts = urlsplit(CLICKHOUSE_DSN)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or 8123), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="apps/api/tests/test_llm_observability_live.py",
    remedy=f"ClickHouse is not answering on {CLICKHOUSE_DSN} — start it with `just dev`, "
    "then `go run ./tools/gateseed` to seed its gen_ai fixtures",
)


@pytest_asyncio.fixture
async def clickhouse() -> AsyncIterator[AsyncClient]:
    parts = urlsplit(CLICKHOUSE_DSN)
    client = await clickhouse_connect.get_async_client(
        host=parts.hostname or "localhost",
        port=parts.port or 8123,
        username=parts.username or "",
        password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def gateseed_tenant(clickhouse: AsyncClient) -> tuple[str, str]:
    """The tenant and project `gateseed` seeds its fixtures into.

    Asking "who sent service_name='gateseed'" needs an unscoped query, which the
    repository rightly refuses, so it is fixture discovery rather than a claim
    the API itself makes.
    """
    result = await clickhouse.query(
        "SELECT tenant_id, project_id FROM spans WHERE service_name = {service:String} "
        "GROUP BY tenant_id, project_id ORDER BY count() DESC LIMIT 1",
        parameters={"service": GATESEED_SERVICE_NAME},
    )
    assert result.result_rows, (
        "no spans from service_name='gateseed' — run `just gate-seed` or `go run "
        "./tools/gateseed` against the dev stack first"
    )
    tenant_id, project_id = result.result_rows[0]
    return str(tenant_id), str(project_id)


async def test_the_three_gateseed_fixtures_are_told_apart_correctly(
    clickhouse: AsyncClient, gateseed_tenant: tuple[str, str]
) -> None:
    """AC29.2, AC29.3, AC29.6 — against real, stored, masked-and-retained data,
    not a fake row standing in for one."""
    tenant_id, project_id = gateseed_tenant
    repository = TenantScopedRepository(clickhouse, tenant_id=tenant_id)

    # Wide enough to hold a fixture run from any point in this development
    # stack's history — the point of this suite is that the fixtures exist at
    # all, not when.
    now = datetime.now(UTC)
    calls = await service.list_llm_calls(
        repository,
        project_id=project_id,
        from_time=now - timedelta(days=30),
        to_time=now + timedelta(minutes=1),
    )

    by_span_name = {call.span_name: call for call in calls.calls}
    assert "chat claude-fable-5-1" in by_span_name, (
        f"the complete fixture's own span name never showed up; groups seen: {sorted(by_span_name)}"
    )
    assert "chat" in by_span_name, "the unlabeled-model fixture never showed up"
    assert "chat gen_ai_test_model" in by_span_name, "the renamed-field fixture never showed up"

    complete = by_span_name["chat claude-fable-5-1"]
    assert complete.model_name == "claude-fable-5-1"
    assert complete.input_tokens is not None and complete.input_tokens > 0
    assert complete.output_tokens is not None and complete.output_tokens > 0

    unlabeled = by_span_name["chat"]
    assert unlabeled.model_name == "unlabeled model"
    assert unlabeled.input_tokens is None
    assert unlabeled.output_tokens is None

    renamed = by_span_name["chat gen_ai_test_model"]
    # The whole point of AC29.6: a model-name field under a name this feature
    # does not read degrades to "unlabeled," not to an error or a silent drop.
    assert renamed.model_name == "unlabeled model"
    assert renamed.input_tokens is not None and renamed.input_tokens > 0


async def test_a_service_filter_narrows_to_that_service_only(
    clickhouse: AsyncClient, gateseed_tenant: tuple[str, str]
) -> None:
    tenant_id, project_id = gateseed_tenant
    repository = TenantScopedRepository(clickhouse, tenant_id=tenant_id)

    now = datetime.now(UTC)
    calls = await service.list_llm_calls(
        repository,
        project_id=project_id,
        from_time=now - timedelta(days=30),
        to_time=now + timedelta(minutes=1),
        service_match="gateseed",
    )

    assert calls.calls, "the service filter found nothing, which proves nothing"
    assert all(call.service_name == "gateseed" for call in calls.calls)
