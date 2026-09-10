"""Wiring for the deletion endpoint: the pools, and the telemetry purge.

Two things live here rather than in `service.py`, and for the same reason the
chokepoint keeps the raw ClickHouse client out of every other module: this is
the one place the deletion touches a driver, and what it does with it is hand it
straight to `TenantScopedPurger`, which binds the tenant. Nothing here runs a
statement. `tests/test_tenant_chokepoint_structure.py` names this module for
that, beside `deps` and `lifespan`.

The whole `Resources` rather than the three pieces separately, because the
erasure runs *after* the response: it cannot use the request-scoped session, and
has to open its own from the factory.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from nexovue_api.auth.dependencies import CurrentIdentity
from nexovue_api.data.clickhouse.base import TenantScopedPurger
from nexovue_api.deps import get_resources
from nexovue_api.lifespan import Resources
from nexovue_api.purge import TenantPurge

ApplicationResources = Annotated[Resources, Depends(get_resources)]


async def get_tenant_purge(
    identity: CurrentIdentity, resources: ApplicationResources
) -> TenantPurge:
    """The telemetry erasure for the caller's own organization, and no other.

    The tenant comes from the session, never from the request, so this endpoint
    cannot be pointed at somebody else's data. It outlives the request safely:
    the client it wraps belongs to the application, not to the connection pool
    entry this request borrowed.
    """
    return TenantPurge(
        TenantScopedPurger(await resources.clickhouse.client(), str(identity.org_id))
    )


TenantErasure = Annotated[TenantPurge, Depends(get_tenant_purge)]
