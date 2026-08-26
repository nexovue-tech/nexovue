"""The dependency probes readiness runs, one per store the API actually uses.

Each is the cheapest question that still proves the connection works end to end:
a round trip through the driver, not a TCP connect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from nexovue_api.health.registry import Check, Probe

if TYPE_CHECKING:  # pragma: no cover — import cycle at runtime, types only here
    from nexovue_api.lifespan import Resources

DEPENDENCY_POSTGRES: Final = "postgres"
DEPENDENCY_REDIS: Final = "redis"
DEPENDENCY_CLICKHOUSE: Final = "clickhouse"

# ClickHouse can be slower to answer than the other two under load, and a
# readiness flap on the query store is more disruptive than a slow probe.
CLICKHOUSE_PROBE_TIMEOUT_SECONDS: Final = 3.0

_SELECT_ONE: Final = text("SELECT 1")


def dependency_checks(resources: Resources) -> list[Check]:
    """Every dependency this service needs to serve a request."""
    return [
        Check(name=DEPENDENCY_POSTGRES, probe=_postgres_probe(resources)),
        Check(name=DEPENDENCY_REDIS, probe=_redis_probe(resources)),
        Check(
            name=DEPENDENCY_CLICKHOUSE,
            probe=_clickhouse_probe(resources),
            timeout_seconds=CLICKHOUSE_PROBE_TIMEOUT_SECONDS,
        ),
    ]


def _postgres_probe(resources: Resources) -> Probe:
    async def probe() -> None:
        # Through the request pool on purpose: an exhausted pool is exactly the
        # condition that should take this replica out of rotation.
        async with resources.engine.connect() as connection:
            await connection.execute(_SELECT_ONE)

    return probe


def _redis_probe(resources: Resources) -> Probe:
    async def probe() -> None:
        await resources.redis.ping()

    return probe


def _clickhouse_probe(resources: Resources) -> Probe:
    async def probe() -> None:
        if not await resources.clickhouse.ping():
            raise ConnectionError("ping returned false")

    return probe
