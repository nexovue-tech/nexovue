"""The real API under uvicorn, for chat's suites that read over real HTTP (F4 plan T2.10,
T2.8, T3.1): its settings read from the environment and validated, as a deployment's are,
against this suite's stores. Rows such a suite reads must be committed, since the server
uses its own connections; each organization made is deleted after.
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Any, Final

import pytest
import uvicorn
from assistant_live import ASYNC_DSN, KEYS, chat_on
from insight_login import DEV_INSIGHT_PASSWORD
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from nexovue_api import insight_account
from nexovue_api.main import create_app
from nexovue_api.settings import Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

CLICKHOUSE_READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)

Asgi = Callable[..., Any]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def serve(
    monkeypatch: pytest.MonkeyPatch, wrap: Callable[[Asgi], Asgi] | None = None
) -> Iterator[str]:
    """The application on its own thread and loop; `wrap` may stand in front of it."""
    for name, value in {
        "POSTGRES_DSN": ASYNC_DSN,
        "CLICKHOUSE_DSN": CLICKHOUSE_READER_DSN,
        "AI_SECRET_KEY": KEYS.current,
        "NEXOVUE_DEPLOYMENT": "self-hosted",
        "SCHEDULER_INSIGHT_REPLICAS": "1",
    }.items():
        monkeypatch.setenv(name, value)
    app: Asgi = create_app(Settings())
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            wrap(app) if wrap is not None else app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        threading.Event().wait(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def paths_seen(app: Asgi, seen: list[str]) -> Asgi:
    """The application, recording every HTTP path that reached the server."""

    async def recorded(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            seen.append(scope.get("raw_path", b"").decode("latin-1") or scope["path"])
        await app(scope, receive, send)

    return recorded


async def provisioned_install() -> AsyncEngine:
    """The AI account made and granted, as `setup` does, and an engine as the owner."""
    os.environ.setdefault(insight_account.PASSWORD_VARIABLE, DEV_INSIGHT_PASSWORD)
    said: list[str] = []
    assert await insight_account.provision(ASYNC_DSN, say=said.append), said
    return create_async_engine(ASYNC_DSN)


async def committed_tenant(
    install: AsyncEngine, made: list[ProvisionedTenant], name: str
) -> ProvisionedTenant:
    async with AsyncSession(install, expire_on_commit=False) as db, db.begin():
        tenant = await provision_tenant(
            db,
            organization_name=name,
            email=f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.test",
            password="correct horse battery staple",
        )
        made.append(tenant)
        await chat_on(db, tenant)
    return tenant


async def delete_tenants(install: AsyncEngine, made: list[ProvisionedTenant]) -> None:
    async with install.begin() as db:
        for tenant in made:
            await db.execute(
                text("DELETE FROM jobs WHERE kwargs->>'org_id' = :o"),
                {"o": str(tenant.organization.id)},
            )
            await db.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": tenant.organization.id}
            )


async def tenants_for(install: AsyncEngine) -> AsyncIterator[list[ProvisionedTenant]]:
    made: list[ProvisionedTenant] = []
    yield made
    await delete_tenants(install, made)
