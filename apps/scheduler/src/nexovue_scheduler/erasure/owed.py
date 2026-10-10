"""Which deleted organizations are still owed an erasure (ADR-014).

The obligation has no table of its own: an organization with `deleted_at` set
and no tenantless `organization.erased` entry *is* one. This module is the only
place a list of tenants for the many-tenant statements can be made, and it
makes one only from that query.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nexovue_platform import erasure
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.types import ARRAY, Uuid

# Privacy law asks for erasure without undue delay. A nightly run meets it; one
# still owed after this long is stuck, and says so.
OVERDUE_AFTER: Final = timedelta(days=3)

# The certificate's target, less the id: the same format the API writes.
_TARGET_PREFIX: Final = erasure.organization_target("")

_OWED: Final = text(
    """
    SELECT o.id FROM organizations o
    WHERE o.deleted_at IS NOT NULL
      AND o.id <> ALL(:attempted)
      AND NOT EXISTS (
          SELECT 1 FROM audit_log a
          WHERE a.org_id IS NULL AND a.action = :erased
            AND a.target = :target_prefix || o.id::text
      )
    ORDER BY o.deleted_at, o.id
    LIMIT :limit
    """
).bindparams(bindparam("attempted", type_=ARRAY(Uuid())))

_OVERDUE: Final = text(
    """
    SELECT count(*) AS overdue, min(o.deleted_at) AS oldest FROM organizations o
    WHERE o.deleted_at < :cutoff
      AND NOT EXISTS (
          SELECT 1 FROM audit_log a
          WHERE a.org_id IS NULL AND a.action = :erased
            AND a.target = :target_prefix || o.id::text
      )
    """
)

_CERTIFY: Final = text(
    "INSERT INTO audit_log (org_id, actor_user_id, action, target) "
    "VALUES (NULL, NULL, :erased, :target)"
)


@dataclass(frozen=True, slots=True)
class OwedTenants:
    """Deleted organizations, read from Postgres in this run. Never built by hand:
    the structure test allows its construction in this module alone."""

    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Overdue:
    count: int
    oldest: datetime | None


async def take_owed(
    connection: AsyncConnection, *, limit: int, attempted: frozenset[str]
) -> OwedTenants:
    """The oldest owed organizations not already attempted in this run."""
    rows = await connection.execute(
        _OWED,
        {
            "attempted": [uuid.UUID(org_id) for org_id in attempted],
            "erased": erasure.ERASED_ACTION,
            "target_prefix": _TARGET_PREFIX,
            "limit": limit,
        },
    )
    return OwedTenants(ids=tuple(str(org_id) for (org_id,) in rows))


async def overdue(connection: AsyncConnection, *, now: datetime) -> Overdue:
    row = (
        await connection.execute(
            _OVERDUE,
            {
                "cutoff": now - OVERDUE_AFTER,
                "erased": erasure.ERASED_ACTION,
                "target_prefix": _TARGET_PREFIX,
            },
        )
    ).one()
    return Overdue(count=int(row.overdue), oldest=row.oldest)


async def certify(connection: AsyncConnection, org_id: str) -> None:
    """The erasure is proved. The one row about this tenant nothing cascades away."""
    await connection.execute(
        _CERTIFY, {"erased": erasure.ERASED_ACTION, "target": erasure.organization_target(org_id)}
    )
