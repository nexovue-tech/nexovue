"""Alert rules and incidents (F21, FR5, FR8, FR9, FR14, FR22, FR24 to FR26).

A rule is validated with the same models the scheduler evaluates it with
(`nexovue_platform.alerts.model`), so a rule saved here is a rule the scheduler
understands. Incidents are written only by the scheduler; here they are read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from nexovue_platform.alerts.model import (
    CheckStateCondition,
    Condition,
    check_subject,
    parse_condition,
    service_subject,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.alerts import queries
from nexovue_api.alerts.evaluation import EvaluationReader
from nexovue_api.alerts.schemas import (
    CoverageResponse,
    EvaluationStatus,
    IncidentListResponse,
    IncidentNotification,
    IncidentSummary,
    OpenIncidentCount,
    RuleChannel,
    RuleListResponse,
    RuleSummary,
    UpdateRuleRequest,
    WriteRuleRequest,
)
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import AlertRule, Incident, NotificationChannel
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
    NotFoundError,
)
from nexovue_api.projects import service as projects

MAX_RULES_PER_PROJECT: Final = 100
RECENT_INCIDENT_DAYS: Final = 30
CANNOT_SAVE: Final = "This rule cannot be saved. Each problem is named with its field."
NOT_FOUND: Final = "No such rule."


async def list_rules(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> RuleListResponse:
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rules = await queries.rules_in_project(session, org_id=org_id, project_id=project_id)
    return RuleListResponse(rules=await _summaries(session, org_id=org_id, rules=rules))


async def get_rule(session: AsyncSession, *, org_id: uuid.UUID, rule_id: uuid.UUID) -> RuleSummary:
    rule = await _require(session, org_id=org_id, rule_id=rule_id)
    [summary] = await _summaries(session, org_id=org_id, rules=[rule])
    return summary


async def create_rule(
    session: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID, request: WriteRuleRequest
) -> RuleSummary:
    await projects.require_project(session, org_id=org_id, project_id=request.project_id)
    existing = await queries.rules_in_project(session, org_id=org_id, project_id=request.project_id)
    if len(existing) >= MAX_RULES_PER_PROJECT:
        raise ConflictError(f"A project may have at most {MAX_RULES_PER_PROJECT} alert rules.")
    condition = await _validated_condition(
        session, org_id=org_id, project_id=request.project_id, raw=request.condition
    )
    await _require_channels(session, org_id=org_id, channel_ids=request.channel_ids)

    rule = AlertRule(
        org_id=org_id,
        project_id=request.project_id,
        name=request.name.strip(),
        condition_kind=condition["kind"],
        condition=condition,
        renotify_minutes=request.renotify_minutes,
        enabled=request.enabled,
    )
    session.add(rule)
    await session.flush()
    await queries.replace_rule_channels(
        session, org_id=org_id, rule_id=rule.id, channel_ids=request.channel_ids
    )
    await session.flush()
    await session.refresh(rule)
    audit.record_alert_rule_created(session, rule=rule, actor_user_id=actor_user_id)
    return await get_rule(session, org_id=org_id, rule_id=rule.id)


async def update_rule(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    rule_id: uuid.UUID,
    request: UpdateRuleRequest,
) -> RuleSummary:
    rule = await _require(session, org_id=org_id, rule_id=rule_id)
    if request.condition is not None:
        condition = await _validated_condition(
            session,
            org_id=org_id,
            project_id=rule.project_id,
            raw=request.condition,
            previously_named=_named_checks(rule),
        )
        if condition["kind"] != rule.condition_kind:
            raise InvalidRequestError(
                CANNOT_SAVE,
                errors=[{"field": "condition.kind", "reason": "a rule's kind cannot change"}],
            )
        rule.condition = condition
    if request.name is not None:
        rule.name = request.name.strip()
    if request.renotify_minutes is not None:
        rule.renotify_minutes = request.renotify_minutes
    if request.clear_renotify:
        rule.renotify_minutes = None
    if request.enabled is not None:
        rule.enabled = request.enabled
    if request.channel_ids is not None:
        await _require_channels(session, org_id=org_id, channel_ids=request.channel_ids)
        await queries.replace_rule_channels(
            session, org_id=org_id, rule_id=rule.id, channel_ids=request.channel_ids
        )
    rule.updated_at = datetime.now(UTC)
    await session.flush()
    await _stop_watching_what_the_rule_no_longer_covers(session, org_id=org_id, rule=rule)
    audit.record_alert_rule_updated(session, rule=rule, actor_user_id=actor_user_id)
    return await get_rule(session, org_id=org_id, rule_id=rule.id)


async def mute_rule(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    rule_id: uuid.UUID,
    until: datetime | None,
) -> RuleSummary:
    """Muted until `until`, ending by itself; None unmutes (FR14). What fires during
    a mute is recorded and never sent, even after (BR5)."""
    rule = await _require(session, org_id=org_id, rule_id=rule_id)
    if until is not None and until <= datetime.now(UTC):
        raise InvalidRequestError(
            "A mute must end in the future.",
            errors=[{"field": "until", "reason": "must be in the future"}],
        )
    rule.muted_until = until
    rule.muted_by = actor_user_id if until is not None else None
    await session.flush()
    audit.record_alert_rule_muted(session, rule=rule, actor_user_id=actor_user_id)
    return await get_rule(session, org_id=org_id, rule_id=rule.id)


async def delete_rule(
    session: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID, rule_id: uuid.UUID
) -> None:
    """Gone for good, the default rule included: nothing recreates it (FR8, AC22)."""
    rule = await _require(session, org_id=org_id, rule_id=rule_id)
    audit.record_alert_rule_deleted(session, rule=rule, actor_user_id=actor_user_id)
    await queries.close_open_incidents(
        session, org_id=org_id, rule_id=rule_id, at=datetime.now(UTC)
    )
    await queries.delete_rule(session, org_id=org_id, rule_id=rule_id)
    await session.flush()


async def close_incidents_of_check(
    session: AsyncSession, *, org_id: uuid.UUID, check_id: uuid.UUID
) -> None:
    """A deleted check is never evaluated again, so nothing else would close these."""
    await queries.close_subject(
        session, org_id=org_id, subject_key=check_subject(check_id), at=datetime.now(UTC)
    )


async def _stop_watching_what_the_rule_no_longer_covers(
    session: AsyncSession, *, org_id: uuid.UUID, rule: AlertRule
) -> None:
    """A disabled or narrowed rule is not evaluated on what it dropped, so its open
    incidents there would stay open for ever."""
    condition = parse_condition(rule.condition)
    dropped = [
        incident.subject_key
        for incident in await queries.open_incidents_of_rule(
            session, org_id=org_id, rule_id=rule.id
        )
        if not rule.enabled or not _covers_subject(condition, incident.subject_key)
    ]
    if dropped:
        await queries.stop_watching(
            session, org_id=org_id, rule_id=rule.id, subject_keys=dropped, at=datetime.now(UTC)
        )


def _covers_subject(condition: Condition, subject_key: str) -> bool:
    kind, _, name = subject_key.partition(":")
    if kind == "check" and isinstance(condition, CheckStateCondition):
        try:
            return condition.covers(uuid.UUID(name))
        except ValueError:
            return False
    if kind == "service" and not isinstance(condition, CheckStateCondition):
        return condition.covers(name)
    # A new error is an event, closed as it opens; anything else is left alone.
    return True


async def list_incidents(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> IncidentListResponse:
    """Open first — what someone opening Alerts came for — then the last 30 days (FR22)."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    since = datetime.now(UTC) - timedelta(days=RECENT_INCIDENT_DAYS)
    open_now = await queries.incidents(
        session, org_id=org_id, project_id=project_id, open_only=True, since=since
    )
    recent = await queries.incidents(
        session, org_id=org_id, project_id=project_id, open_only=False, since=since
    )
    summaries = await _incident_summaries(session, org_id=org_id, incidents=[*open_now, *recent])
    return IncidentListResponse(open=summaries[: len(open_now)], recent=summaries[len(open_now) :])


async def open_incident_count(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> OpenIncidentCount:
    """A count, not the list: every screen asks for it, every fifteen seconds."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    counts = await queries.open_incident_counts(session, org_id=org_id, project_id=project_id)
    return OpenIncidentCount(open=sum(counts.values()), critical=counts.get("critical", 0))


async def get_incident(
    session: AsyncSession, *, org_id: uuid.UUID, incident_id: uuid.UUID
) -> IncidentSummary:
    incident = await queries.find_incident(session, org_id=org_id, incident_id=incident_id)
    if incident is None:
        raise NotFoundError("No such incident.")
    [summary] = await _incident_summaries(session, org_id=org_id, incidents=[incident])
    return summary


async def coverage_of_check(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, check_id: uuid.UUID
) -> CoverageResponse:
    """The rules that would alert on this check, and its open incidents (FR24)."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    rules = [
        rule
        for rule in await queries.rules_in_project(session, org_id=org_id, project_id=project_id)
        if rule.condition_kind == "check_state"
        and CheckStateCondition.model_validate(rule.condition).covers(check_id)
    ]
    open_now = await queries.incidents(
        session,
        org_id=org_id,
        project_id=project_id,
        open_only=True,
        since=datetime.now(UTC),
        subject_keys=[check_subject(check_id)],
    )
    return CoverageResponse(
        rules=await _summaries(session, org_id=org_id, rules=rules),
        open_incidents=await _incident_summaries(session, org_id=org_id, incidents=open_now),
    )


async def coverage_of_service(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, service: str
) -> CoverageResponse:
    """The service rules that watch this service, and its open incidents (FR24)."""
    await projects.require_project(session, org_id=org_id, project_id=project_id)
    covering = []
    for rule in await queries.rules_in_project(session, org_id=org_id, project_id=project_id):
        if rule.condition_kind == "check_state":
            continue
        condition = parse_condition(rule.condition)
        if not isinstance(condition, CheckStateCondition) and condition.covers(service):
            covering.append(rule)
    open_now = await queries.incidents(
        session,
        org_id=org_id,
        project_id=project_id,
        open_only=True,
        since=datetime.now(UTC),
        subject_keys=[service_subject(service)],
    )
    return CoverageResponse(
        rules=await _summaries(session, org_id=org_id, rules=covering),
        open_incidents=await _incident_summaries(session, org_id=org_id, incidents=open_now),
    )


async def evaluation_status(reader: EvaluationReader | None) -> EvaluationStatus:
    if reader is None:
        raise DependencyUnavailableError(
            "Evaluation status is not available: the API cannot reach the scheduler's broker."
        )
    return await reader.status()


async def _require(session: AsyncSession, *, org_id: uuid.UUID, rule_id: uuid.UUID) -> AlertRule:
    rule = await queries.find_rule(session, org_id=org_id, rule_id=rule_id)
    if rule is None:
        raise NotFoundError(NOT_FOUND)
    return rule


def _named_checks(rule: AlertRule) -> frozenset[uuid.UUID]:
    if rule.condition_kind != "check_state":
        return frozenset()
    return frozenset(CheckStateCondition.model_validate(rule.condition).check_ids or ())


async def _validated_condition(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    raw: dict[str, Any],
    previously_named: frozenset[uuid.UUID] = frozenset(),
) -> dict[str, Any]:
    """`previously_named` are the checks the rule already named: one deleted since is
    dropped rather than refused, since the console cannot show it to untick."""
    try:
        condition = parse_condition(raw)
    except ValidationError as invalid:
        raise InvalidRequestError(
            CANNOT_SAVE,
            errors=[
                {
                    "field": ".".join(("condition", *(str(part) for part in problem["loc"]))),
                    "reason": problem["msg"],
                }
                for problem in invalid.errors()
            ],
        ) from invalid
    except ValueError as invalid:
        raise InvalidRequestError(
            CANNOT_SAVE, errors=[{"field": "condition.kind", "reason": str(invalid)}]
        ) from invalid

    if isinstance(condition, CheckStateCondition) and condition.check_ids is not None:
        found = await queries.existing_check_ids(
            session, org_id=org_id, project_id=project_id, check_ids=condition.check_ids
        )
        missing = [
            str(check_id)
            for check_id in condition.check_ids
            if check_id not in found and check_id not in previously_named
        ]
        if missing:
            raise InvalidRequestError(
                CANNOT_SAVE,
                errors=[
                    {
                        "field": "condition.check_ids",
                        "reason": f"no such check in this project: {', '.join(missing)}",
                    }
                ],
            )
        kept = [check_id for check_id in condition.check_ids if check_id in found]
        if not kept:
            raise InvalidRequestError(
                CANNOT_SAVE,
                errors=[{"field": "condition.check_ids", "reason": "choose at least one check"}],
            )
        condition = condition.model_copy(update={"check_ids": kept})
    return condition.model_dump(mode="json")


async def _require_channels(
    session: AsyncSession, *, org_id: uuid.UUID, channel_ids: list[uuid.UUID]
) -> None:
    wanted = set(channel_ids)
    found = {
        channel.id
        for channel in await queries.channels_by_id(
            session, org_id=org_id, channel_ids=list(wanted)
        )
    }
    if wanted - found:
        raise InvalidRequestError(
            CANNOT_SAVE, errors=[{"field": "channel_ids", "reason": "no such channel"}]
        )


async def _summaries(
    session: AsyncSession, *, org_id: uuid.UUID, rules: list[AlertRule]
) -> list[RuleSummary]:
    channels = await queries.channels_of_rules(
        session, org_id=org_id, rule_ids=[rule.id for rule in rules]
    )
    summaries = []
    for rule in rules:
        covers_nothing = False
        if rule.condition_kind == "check_state":
            named = CheckStateCondition.model_validate(rule.condition).check_ids
            if named:
                found = await queries.existing_check_ids(
                    session, org_id=org_id, project_id=rule.project_id, check_ids=named
                )
                covers_nothing = not found
        summaries.append(
            RuleSummary(
                id=rule.id,
                project_id=rule.project_id,
                name=rule.name,
                condition_kind=rule.condition_kind,
                condition=rule.condition,
                channels=[_rule_channel(channel) for channel in channels.get(rule.id, [])],
                renotify_minutes=rule.renotify_minutes,
                enabled=rule.enabled,
                muted_until=rule.muted_until,
                muted_by=rule.muted_by,
                is_default=rule.is_default,
                notifying_nobody=not channels.get(rule.id),
                covers_nothing=covers_nothing,
                created_at=rule.created_at,
                updated_at=rule.updated_at,
            )
        )
    return summaries


def _rule_channel(channel: NotificationChannel) -> RuleChannel:
    return RuleChannel(id=channel.id, kind=channel.kind, name=channel.name)


async def _incident_summaries(
    session: AsyncSession, *, org_id: uuid.UUID, incidents: list[Incident]
) -> list[IncidentSummary]:
    notifications = await queries.notifications_of(
        session, org_id=org_id, incident_ids=list({incident.id for incident in incidents})
    )
    checks = {incident.id: _check_of(incident) for incident in incidents}
    existing: set[uuid.UUID] = set()
    for project_id in {incident.project_id for incident in incidents if checks[incident.id]}:
        existing |= await queries.existing_check_ids(
            session,
            org_id=org_id,
            project_id=project_id,
            check_ids=[
                check
                for incident in incidents
                if incident.project_id == project_id and (check := checks[incident.id])
            ],
        )
    return [
        IncidentSummary(
            id=incident.id,
            project_id=incident.project_id,
            rule_id=incident.rule_id,
            rule_name=incident.rule_name,
            condition_kind=incident.condition_kind,
            subject_key=incident.subject_key,
            subject_label=incident.subject_label,
            severity=incident.severity,
            opened_at=incident.opened_at,
            resolved_at=incident.resolved_at,
            is_event=incident.is_event,
            flapping=incident.flapping,
            muted=incident.muted,
            detail=incident.detail,
            check_deleted=checks[incident.id] is not None and checks[incident.id] not in existing,
            notifications=[
                IncidentNotification(
                    id=notification.id,
                    channel_id=channel.id,
                    channel_name=channel.name,
                    channel_kind=channel.kind,
                    kind=notification.kind,
                    state=notification.state,
                    reason=notification.outcome_reason,
                    delivered_at=notification.delivered_at,
                )
                for notification, channel in notifications.get(incident.id, [])
            ],
        )
        for incident in incidents
    ]


def _check_of(incident: Incident) -> uuid.UUID | None:
    """The check an incident is about, if it is about one."""
    check_id = incident.detail.get("check_id")
    if incident.condition_kind != "check_state" or not check_id:
        return None
    try:
        return uuid.UUID(str(check_id))
    except ValueError:
        return None
