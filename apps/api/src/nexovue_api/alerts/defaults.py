"""What every organization and project is created with, so the first check that
goes down tells somebody without any setup (US21.1).

Called from inside the transactions that create an organization and a project,
so a tenant never exists without its owners channel, nor a project without its
default rule. Nothing recreates a default rule that was deleted (FR8, AC22):
these run only at creation.
"""

from __future__ import annotations

import uuid

from nexovue_platform.alerts.model import (
    DEFAULT_RULE_CONDITION,
    DEFAULT_RULE_NAME,
    OWNERS_CHANNEL_NAME,
)
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.alerts import queries
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import AlertRule, AlertRuleChannel, NotificationChannel


async def provision_organization(
    session: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID
) -> NotificationChannel:
    """The organization's owners, as a channel (FR2): muted, never deleted."""
    channel = NotificationChannel(org_id=org_id, kind="owners", name=OWNERS_CHANNEL_NAME, config={})
    session.add(channel)
    await session.flush()
    audit.record_notification_channel_created(session, channel=channel, actor_user_id=actor_user_id)
    return channel


async def provision_project(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> AlertRule:
    """Any check down, and its recovery, emailed to the owners (FR8, D3)."""
    owners = await queries.owners_channel(session, org_id=org_id)
    if owners is None:
        # Every organization is created with one and it cannot be deleted, so this
        # is a defect, not a state to tolerate: a project whose default rule
        # silently notifies nobody is the failure this feature exists to end.
        raise RuntimeError(f"organization {org_id} has no owners channel")

    rule = AlertRule(
        org_id=org_id,
        project_id=project_id,
        name=DEFAULT_RULE_NAME,
        condition_kind=DEFAULT_RULE_CONDITION.kind,
        condition=DEFAULT_RULE_CONDITION.model_dump(mode="json"),
        is_default=True,
    )
    session.add(rule)
    await session.flush()
    session.add(AlertRuleChannel(org_id=org_id, rule_id=rule.id, channel_id=owners.id))
    audit.record_alert_rule_created(session, rule=rule, actor_user_id=actor_user_id)
    return rule
