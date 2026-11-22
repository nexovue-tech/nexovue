"""The tracker account the connections jobs run as in these suites (F22 plan T8.7).

`scheduler-connections` signs in as `nexovue_connections`, so its jobs run as that account
here too: a statement its grants do not allow fails in the suite, not in production. The rows
a test arranges are still written by the install's own account (`engine`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Final

import pytest
from alert_fixtures import ASYNC_DSN
from connections_login import DEV_CONNECTIONS_PASSWORD, connections_dsn
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_api import connections_account

CONNECTIONS_ASYNC_DSN: Final = connections_dsn(ASYNC_DSN)
# Under the account's connection limit with a few suites at once (`connections_account`).
_POOL: Final = 2


@pytest.fixture
async def connections_engine(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """A real login as the tracker account, after the real `setup` step has prepared it."""
    monkeypatch.setenv(connections_account.PASSWORD_VARIABLE, DEV_CONNECTIONS_PASSWORD)
    said: list[str] = []
    assert await connections_account.provision(ASYNC_DSN, say=said.append), said
    created = create_async_engine(CONNECTIONS_ASYNC_DSN, pool_size=_POOL, max_overflow=_POOL)
    try:
        yield created
    finally:
        await created.dispose()
