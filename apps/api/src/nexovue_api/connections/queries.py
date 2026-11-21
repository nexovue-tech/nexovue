"""The connection statements the API runs (F22). Every one binds the caller's `org_id`,
except :func:`live_webhook_connection`, the webhook's first read (AC139): a webhook's
caller has proved nothing yet, so the organization is read off the connection whose secret
then matched, and every statement after it binds that.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from nexovue_platform.connections import (
    DISCONNECTED,
    GITHUB,
    NOT_FOLLOWED,
    SETTING_UP,
    STOPPED,
    SYNCED,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    Connection,
    ConnectionRepository,
    FindingLink,
    JobResult,
    Organization,
)


async def live_connections(session: AsyncSession, *, org_id: uuid.UUID) -> list[Connection]:
    rows = await session.scalars(
        select(Connection)
        .where(Connection.org_id == org_id, Connection.state != DISCONNECTED)
        .order_by(Connection.kind)
    )
    return list(rows)


async def places_of(
    session: AsyncSession, *, org_id: uuid.UUID, connection_ids: list[uuid.UUID]
) -> list[ConnectionRepository]:
    if not connection_ids:
        return []
    rows = await session.scalars(
        select(ConnectionRepository)
        .where(
            ConnectionRepository.org_id == org_id,
            ConnectionRepository.connection_id.in_(connection_ids),
        )
        .order_by(ConnectionRepository.key)
    )
    return list(rows)


async def lock_live_connection(
    session: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> Connection | None:
    # `FOR NO KEY UPDATE` (D78's rule): no key column changes, so a place's foreign-key check
    # on this row never waits behind a person's act.
    found: Connection | None = await session.scalar(
        select(Connection)
        .where(
            Connection.org_id == org_id,
            Connection.id == connection_id,
            Connection.state != DISCONNECTED,
        )
        .with_for_update(key_share=True)
    )
    return found


async def live_github_connection(session: AsyncSession, *, org_id: uuid.UUID) -> Connection | None:
    found: Connection | None = await session.scalar(
        select(Connection).where(
            Connection.org_id == org_id,
            Connection.kind == GITHUB,
            Connection.state != DISCONNECTED,
        )
    )
    return found


async def lock_setup(
    session: AsyncSession, *, org_id: uuid.UUID, state_hash: str
) -> Connection | None:
    """The setup this `state` was issued for, in the caller's organization, if unused."""
    found: Connection | None = await session.scalar(
        select(Connection)
        .where(
            Connection.org_id == org_id,
            Connection.kind == GITHUB,
            Connection.state == SETTING_UP,
            Connection.setup_state_hash == state_hash,
        )
        .with_for_update(key_share=True)
    )
    return found


async def retire_abandoned_setups(
    session: AsyncSession, *, org_id: uuid.UUID, now: datetime
) -> None:
    """A setup that never got an App and is past its hour (AC76). One with an App is kept:
    GitHub holds that App, and *Stop setting up* is the person's to press."""
    await session.execute(
        update(Connection)
        .where(
            Connection.org_id == org_id,
            Connection.kind == GITHUB,
            Connection.state == SETTING_UP,
            Connection.github_app_id.is_(None),
            Connection.setup_expires_at <= now,
        )
        .values(state=DISCONNECTED, setup_state_hash=None, sealed_manifest_code=None)
        .execution_options(synchronize_session=False)
    )


async def organization_slug(session: AsyncSession, *, org_id: uuid.UUID) -> str:
    slug = await session.scalar(select(Organization.slug).where(Organization.id == org_id))
    return slug or "app"


async def test_outcome(
    session: AsyncSession, *, org_id: uuid.UUID, request_id: uuid.UUID, kind: str
) -> dict[str, object] | None:
    payload: dict[str, object] | None = await session.scalar(
        select(JobResult.payload).where(
            JobResult.request_id == request_id,
            JobResult.org_id == org_id,
            JobResult.kind == kind,
        )
    )
    return payload


async def synced_link_counts(
    session: AsyncSession, *, org_id: uuid.UUID, connection_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Live synced links per connection: what a disconnect would stop (FR32, AC114)."""
    if not connection_ids:
        return {}
    rows = await session.execute(
        select(FindingLink.connection_id, func.count())
        .where(
            FindingLink.org_id == org_id,
            FindingLink.connection_id.in_(connection_ids),
            FindingLink.role == SYNCED,
            FindingLink.sync_status != STOPPED,
        )
        .group_by(FindingLink.connection_id)
    )
    return {connection_id: int(count) for connection_id, count in rows.all() if connection_id}


async def stop_links(session: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    """A disconnect's links: stopped, nothing pending, no hold, no facts kept (FR32)."""
    await session.execute(
        update(FindingLink)
        .where(
            FindingLink.org_id == org_id,
            FindingLink.connection_id == connection_id,
            FindingLink.sync_status != STOPPED,
        )
        .values(
            sync_status=STOPPED,
            next_sync_at=None,
            frozen_facts=None,
            wanted_state=None,
            comment_wanted=None,
            tracker_closed_at=None,
        )
        .execution_options(synchronize_session=False)
    )


async def live_webhook_connection(
    session: AsyncSession, *, connection_id: uuid.UUID
) -> Connection | None:
    """The connection a webhook names, by id alone.

    Not scoped by `org_id`, deliberately: the caller is an unauthenticated tracker that has
    proved nothing yet, and the id in the path is all it has said. The signature over the
    secret sealed on this row is what proves it, and the organization is then read off this
    row, never off the request (AC139, as `live_webhook_secrets` for deploys).
    """
    found: Connection | None = await session.scalar(
        select(Connection).where(Connection.id == connection_id, Connection.state != DISCONNECTED)
    )
    return found


async def ring_links(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    external_id: str,
    now: datetime,
    read_gap: timedelta,
) -> None:
    """Bring forward the next read of every link to this issue that is already followed.

    Only ever sooner (ADR-035): a link with no read scheduled is left alone, because that is
    a link not followed, stopped, gone, or waiting for a person after a refused write, and a
    webhook must revive none of them. A link in backoff keeps its backoff (FR39). The read
    is set to now, or to `read_gap` after the last read if that is later, so a replayed
    delivery costs at most two reads a minute (AC48) and a change made just after a read
    still arrives within the minute (AC46). A link already due sooner keeps its place.
    """
    soonest = func.greatest(now, func.coalesce(FindingLink.last_synced_at + read_gap, now))
    await session.execute(
        update(FindingLink)
        .where(
            FindingLink.org_id == org_id,
            FindingLink.connection_id == connection_id,
            FindingLink.external_id == external_id,
            FindingLink.sync_status.not_in((NOT_FOLLOWED, STOPPED)),
            FindingLink.failures == 0,
            FindingLink.next_sync_at > soonest,
        )
        .values(next_sync_at=soonest)
        .execution_options(synchronize_session=False)
    )
