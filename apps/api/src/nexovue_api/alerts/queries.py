"""Postgres reads for alerting. Every one binds `org_id`."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Final

from nexovue_platform.alerts.channels import READ_BY_MACHINES
from sqlalchemy import DateTime, and_, delete, func, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.data.postgres.models import (
    AlertEvaluation,
    AlertRule,
    AlertRuleChannel,
    Check,
    Incident,
    Notification,
    NotificationChannel,
)

# How many incidents one read returns. Named so the service can say whether the
# cap bit (F36, AC20) rather than repeating the number.
INCIDENT_PAGE: Final = 200


async def owners_channel(session: AsyncSession, *, org_id: uuid.UUID) -> NotificationChannel | None:
    statement = select(NotificationChannel).where(
        NotificationChannel.org_id == org_id, NotificationChannel.kind == "owners"
    )
    found: NotificationChannel | None = await session.scalar(statement)
    return found


async def rules_in_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[AlertRule]:
    statement = (
        select(AlertRule)
        .where(AlertRule.org_id == org_id, AlertRule.project_id == project_id)
        .order_by(AlertRule.is_default.desc(), AlertRule.name)
    )
    return list(await session.scalars(statement))


async def find_rule(
    session: AsyncSession, *, org_id: uuid.UUID, rule_id: uuid.UUID
) -> AlertRule | None:
    statement = select(AlertRule).where(AlertRule.org_id == org_id, AlertRule.id == rule_id)
    found: AlertRule | None = await session.scalar(statement)
    return found


async def channels_of_rules(
    session: AsyncSession, *, org_id: uuid.UUID, rule_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[NotificationChannel]]:
    grouped: dict[uuid.UUID, list[NotificationChannel]] = defaultdict(list)
    if not rule_ids:
        return grouped
    statement = (
        select(AlertRuleChannel.rule_id, NotificationChannel)
        .join(NotificationChannel, NotificationChannel.id == AlertRuleChannel.channel_id)
        .where(
            AlertRuleChannel.org_id == org_id,
            NotificationChannel.org_id == org_id,
            AlertRuleChannel.rule_id.in_(rule_ids),
        )
        .order_by(NotificationChannel.name)
    )
    for rule_id, channel in (await session.execute(statement)).all():
        grouped[rule_id].append(channel)
    return grouped


async def channels_by_id(
    session: AsyncSession, *, org_id: uuid.UUID, channel_ids: list[uuid.UUID]
) -> list[NotificationChannel]:
    statement = select(NotificationChannel).where(
        NotificationChannel.org_id == org_id, NotificationChannel.id.in_(channel_ids)
    )
    return list(await session.scalars(statement))


async def existing_check_ids(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, check_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    statement = select(Check.id).where(
        Check.org_id == org_id, Check.project_id == project_id, Check.id.in_(check_ids)
    )
    return set(await session.scalars(statement))


async def replace_rule_channels(
    session: AsyncSession, *, org_id: uuid.UUID, rule_id: uuid.UUID, channel_ids: list[uuid.UUID]
) -> None:
    await session.execute(
        delete(AlertRuleChannel).where(
            AlertRuleChannel.org_id == org_id, AlertRuleChannel.rule_id == rule_id
        )
    )
    session.add_all(
        AlertRuleChannel(org_id=org_id, rule_id=rule_id, channel_id=channel_id)
        for channel_id in dict.fromkeys(channel_ids)
    )


async def _close(
    session: AsyncSession, *, org_id: uuid.UUID, at: datetime, where: list[Any]
) -> None:
    """Close the open incidents matching `where`, without recovering: nothing will
    evaluate them again. Machines that were sent them are told they closed."""
    closed = list(
        await session.scalars(
            update(Incident)
            .where(Incident.org_id == org_id, Incident.resolved_at.is_(None), *where)
            .values(resolved_at=at)
            .returning(Incident.id)
        )
    )
    if closed:
        await _tell_machines_it_closed(session, org_id=org_id, incident_ids=closed, at=at)


async def _tell_machines_it_closed(
    session: AsyncSession, *, org_id: uuid.UUID, incident_ids: list[uuid.UUID], at: datetime
) -> None:
    """A `closed` notification for each PagerDuty or webhook channel that was sent
    the opening — otherwise the alert stays open there for ever. People are not
    told: nothing recovered, and a person chose the change (spec D8). Each incident
    closes once, so there is no second `closed` to collide with."""
    when = literal(at, type_=DateTime(timezone=True))
    told = (
        select(
            Incident.org_id,
            Incident.project_id,
            Incident.id,
            Notification.channel_id,
            literal("closed"),
            literal(0),
            Incident.severity,
            when,
            when,
        )
        .distinct()
        .join(
            Notification,
            and_(Notification.incident_id == Incident.id, Notification.org_id == Incident.org_id),
        )
        .join(
            NotificationChannel,
            and_(
                NotificationChannel.id == Notification.channel_id,
                NotificationChannel.org_id == Incident.org_id,
            ),
        )
        .where(
            Incident.org_id == org_id,
            Incident.id.in_(incident_ids),
            Notification.kind.in_(("opened", "flapping")),
            Notification.state != "dropped",
            NotificationChannel.kind.in_(sorted(READ_BY_MACHINES)),
        )
    )
    await session.execute(
        insert(Notification).from_select(
            [
                Notification.org_id,
                Notification.project_id,
                Notification.incident_id,
                Notification.channel_id,
                Notification.kind,
                Notification.sequence,
                Notification.severity,
                Notification.deliver_after,
                Notification.created_at,
            ],
            told,
        )
    )


async def close_open_incidents(
    session: AsyncSession, *, org_id: uuid.UUID, rule_id: uuid.UUID, at: datetime
) -> None:
    """A deleted rule's open incidents end with it."""
    await _close(session, org_id=org_id, at=at, where=[Incident.rule_id == rule_id])


async def open_incidents_of_rule(
    session: AsyncSession, *, org_id: uuid.UUID, rule_id: uuid.UUID
) -> list[Incident]:
    statement = select(Incident).where(
        Incident.org_id == org_id, Incident.rule_id == rule_id, Incident.resolved_at.is_(None)
    )
    return list(await session.scalars(statement))


async def stop_watching(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    rule_id: uuid.UUID,
    subject_keys: list[str],
    at: datetime,
) -> None:
    """Close the rule's open incidents on these subjects, as a deleted rule's are,
    and forget its threshold positions there, so watching them again starts from
    nothing rather than from a *firing* with no incident behind it."""
    await _close(
        session,
        org_id=org_id,
        at=at,
        where=[Incident.rule_id == rule_id, Incident.subject_key.in_(subject_keys)],
    )
    await session.execute(
        delete(AlertEvaluation).where(
            AlertEvaluation.org_id == org_id,
            AlertEvaluation.rule_id == rule_id,
            AlertEvaluation.subject_key.in_(subject_keys),
        )
    )


async def close_subject(
    session: AsyncSession, *, org_id: uuid.UUID, subject_key: str, at: datetime
) -> None:
    """Every rule's open incident on a subject that no longer exists."""
    await _close(session, org_id=org_id, at=at, where=[Incident.subject_key == subject_key])


async def close_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, at: datetime
) -> None:
    """A deleted project's rules go with it, as its credentials do: the scheduler
    never evaluates it again, so its open incidents would never close, and its rules
    would still be named on the channels they sent to. Incidents stay as history."""
    await _close(session, org_id=org_id, at=at, where=[Incident.project_id == project_id])
    await session.execute(
        delete(AlertRule).where(AlertRule.org_id == org_id, AlertRule.project_id == project_id)
    )


async def delete_rule(session: AsyncSession, *, org_id: uuid.UUID, rule_id: uuid.UUID) -> None:
    await session.execute(
        delete(AlertRule).where(AlertRule.org_id == org_id, AlertRule.id == rule_id)
    )


async def incidents(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    open_only: bool,
    since: datetime,
    subject_keys: list[str] | None = None,
    limit: int = INCIDENT_PAGE,
) -> list[Incident]:
    statement = select(Incident).where(Incident.org_id == org_id, Incident.project_id == project_id)
    if open_only:
        statement = statement.where(Incident.resolved_at.is_(None))
    else:
        statement = statement.where(Incident.opened_at >= since)
    if subject_keys is not None:
        statement = statement.where(Incident.subject_key.in_(subject_keys))
    return list(await session.scalars(statement.order_by(Incident.opened_at.desc()).limit(limit)))


async def open_incident_counts(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> dict[str, int]:
    """Open incidents by severity, served by the partial index on open rows."""
    statement = (
        select(Incident.severity, func.count())
        .where(
            Incident.org_id == org_id,
            Incident.project_id == project_id,
            Incident.resolved_at.is_(None),
        )
        .group_by(Incident.severity)
    )
    rows = (await session.execute(statement)).tuples().all()
    return dict(rows)


async def find_incident(
    session: AsyncSession, *, org_id: uuid.UUID, incident_id: uuid.UUID
) -> Incident | None:
    statement = select(Incident).where(Incident.org_id == org_id, Incident.id == incident_id)
    found: Incident | None = await session.scalar(statement)
    return found


async def notifications_of(
    session: AsyncSession, *, org_id: uuid.UUID, incident_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[Notification, NotificationChannel]]]:
    grouped: dict[uuid.UUID, list[tuple[Notification, NotificationChannel]]] = defaultdict(list)
    if not incident_ids:
        return grouped
    statement = (
        select(Notification, NotificationChannel)
        .join(NotificationChannel, NotificationChannel.id == Notification.channel_id)
        .where(
            Notification.org_id == org_id,
            NotificationChannel.org_id == org_id,
            Notification.incident_id.in_(incident_ids),
        )
        .order_by(Notification.created_at)
    )
    for notification, channel in (await session.execute(statement)).all():
        grouped[notification.incident_id].append((notification, channel))
    return grouped
