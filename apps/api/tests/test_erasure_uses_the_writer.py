"""Erasure is the one API path that writes to ClickHouse, and it alone holds the
writer (docs/changes/clickhouse-protects-itself).

Every other read connects as the read-only account. If the purge were built from
the reader, deleting an organization would fail at the store and leave its
telemetry behind; if a read path were handed the writer, the read-only account
would protect nothing. This pins the one place the two meet.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from nexovue_api.organizations.dependencies import get_tenant_purge


class _Connection:
    def __init__(self, name: str) -> None:
        self.client_object: Any = SimpleNamespace(name=name)

    async def client(self) -> Any:
        return self.client_object


async def test_the_purge_is_built_from_the_writer_never_the_reader() -> None:
    reader, writer = _Connection("reader"), _Connection("writer")
    resources: Any = SimpleNamespace(clickhouse=reader, clickhouse_writer=writer)
    identity: Any = SimpleNamespace(org_id=uuid.uuid4())

    purge = await get_tenant_purge(identity, resources)

    assert purge._purger._client is writer.client_object, (
        "the erasure was handed the reader, so deleting an organization cannot remove its telemetry"
    )
