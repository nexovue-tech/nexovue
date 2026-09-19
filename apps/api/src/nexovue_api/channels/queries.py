"""Postgres reads and writes for notification channels. Every one binds `org_id`."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AlertRule,
    AlertRuleChannel,
    Incident,
    Notification,
    NotificationAttempt,
    NotificationChannel,
)

DELIVERY_LOG_LIMIT: Final = 100


def add(session: AsyncSession, channel: NotificationChannel) -> None:
    session.add(channel)


async def list_channels(session: AsyncSession, *, org_id: uuid.UUID) -> list[NotificationChannel]:
    statement = (
        select(NotificationChannel)
        .where(NotificationChannel.org_id == org_id)
        .order_by(NotificationChannel.kind != "owners", NotificationChannel.name)
    )
    return list(await session.scalars(statement))


async def find_channel(
    session: AsyncSession, *, org_id: uuid.UUID, channel_id: uuid.UUID
) -> NotificationChannel | None:
    statement = select(NotificationChannel).where(
        NotificationChannel.org_id == org_id, NotificationChannel.id == channel_id
    )
    found: NotificationChannel | None = await session.scalar(statement)
    return found


async def rules_by_channel(
    session: AsyncSession, *, org_id: uuid.UUID
) -> dict[uuid.UUID, list[AlertRule]]:
    statement = (
        select(AlertRuleChannel.channel_id, AlertRule)
        .join(AlertRule, AlertRule.id == AlertRuleChannel.rule_id)
        .where(AlertRuleChannel.org_id == org_id, AlertRule.org_id == org_id)
        .order_by(AlertRule.name)
    )
    grouped: dict[uuid.UUID, list[AlertRule]] = defaultdict(list)
    for channel_id, rule in (await session.execute(statement)).all():
        grouped[channel_id].append(rule)
    return grouped


async def delete_channel(
    session: AsyncSession, *, org_id: uuid.UUID, channel_id: uuid.UUID
) -> None:
    await session.execute(
        delete(NotificationChannel).where(
            NotificationChannel.org_id == org_id, NotificationChannel.id == channel_id
        )
    )


async def delivery_log(
    session: AsyncSession, *, org_id: uuid.UUID, channel_id: uuid.UUID, since: datetime
) -> list[tuple[NotificationAttempt, list[str]]]:
    """The channel's attempts, newest first, each with what it was about."""
    attempts = list(
        await session.scalars(
            select(NotificationAttempt)
            .where(
                NotificationAttempt.org_id == org_id,
                NotificationAttempt.channel_id == channel_id,
                NotificationAttempt.attempted_at >= since,
            )
            .order_by(NotificationAttempt.attempted_at.desc())
            .limit(DELIVERY_LOG_LIMIT)
        )
    )
    batches = {attempt.batch_id for attempt in attempts}
    about: dict[uuid.UUID, list[str]] = defaultdict(list)
    if batches:
        described = await session.execute(
            select(Notification.batch_id, Notification.kind, Incident.subject_label)
            .join(Incident, Incident.id == Notification.incident_id)
            .where(
                Notification.org_id == org_id,
                Incident.org_id == org_id,
                Notification.batch_id.in_(batches),
            )
        )
        for batch_id, kind, subject in described.all():
            about[batch_id].append(f"{subject}: {kind}")
    return [(attempt, about[attempt.batch_id]) for attempt in attempts]


def summary_fields(channel: NotificationChannel) -> dict[str, Any]:
    return {
        "id": channel.id,
        "kind": channel.kind,
        "name": channel.name,
        "config": channel.config,
        "secret_set": channel.sealed_secret is not None,
        "health": channel.health,
        "failing_since": channel.failing_since,
        "last_delivered_at": channel.last_delivered_at,
        "muted_until": channel.muted_until,
        "muted_by": channel.muted_by,
        "quiet_hours": channel.quiet_hours,
        "created_at": channel.created_at,
    }
