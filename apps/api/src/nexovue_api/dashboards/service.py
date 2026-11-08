"""Saving and reading dashboards, and the Postgres half of a dashboard page (F20).

**Any working member may change any dashboard** (BR7), which is safe only with a way
back: each save that changes the title or definition keeps the version it replaced,
and a deletion can be undone for 30 days (D19). A version is trimmed only when it is
both past the newest 20 and older than 30 days, under a ceiling of 200, so 20 quick
saves by a compromised account cannot erase the last good one (FR51, AC125).

**A save is one transaction**: the row, its kept version and its audit entry commit
together through the request's session (design, *Failure handling*).

The caps here are the spec's *guesses* until plan T5.7, and exist for protection only.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.dependencies import Identity
from nexovue_api.dashboards import queries
from nexovue_api.dashboards.compile import compile_for_save
from nexovue_api.dashboards.definition import (
    DashboardDocument,
    canonical_json,
    check_size,
    link_hosts,
    parse_stored,
)
from nexovue_api.dashboards.ranges import RangeId, window_for
from nexovue_api.dashboards.schemas import (
    ContextFinding,
    ContextProject,
    DashboardContext,
    DashboardListResponse,
    DashboardResponse,
    DashboardSummary,
    DashboardTemplate,
    DashboardTemplatesResponse,
    OpenFindings,
    PlanRetention,
    ReplaceDashboard,
)
from nexovue_api.dashboards.templates import TEMPLATES
from nexovue_api.data.postgres.models import Dashboard, DashboardVersion, Project
from nexovue_api.detection import queries as detection_queries
from nexovue_api.detection import service as detection
from nexovue_api.errors import problems
from nexovue_api.errors.exceptions import ConflictError, InternalError, NotFoundError
from nexovue_api.projects import queries as project_queries
from nexovue_api.usage import queries as usage_queries

# FR9: protection, never an upsell. Deleted dashboards count towards the second, so
# creating and deleting cannot grow rows for 30 days.
LIVE_DASHBOARDS_CAP: Final = 1_000
ALL_DASHBOARDS_CAP: Final = 2_000
# FR51.
VERSIONS_KEPT_NEWEST: Final = 20
VERSIONS_KEPT_FOR: Final = timedelta(days=30)
VERSIONS_CEILING: Final = 200
# FR37: "last opened" is true to within an hour, and costs at most one write an hour.
LAST_OPENED_EVERY: Final = timedelta(hours=1)

NO_SUCH_DASHBOARD: Final = "This dashboard does not exist or is no longer available."
NO_SUCH_PROJECT: Final = "This link is for a project you cannot open."


class DashboardNotFoundError(NotFoundError):
    """No such live dashboard in this organization, whether it exists elsewhere or not."""

    problem = problems.DASHBOARD_NOT_FOUND


class ProjectNotFoundError(NotFoundError):
    """No such project in this organization: the dashboard is fine, the project is not."""

    problem = problems.PROJECT_NOT_FOUND


# ------------------------------------------------------------------ reading


async def list_dashboards(db: AsyncSession, *, org_id: uuid.UUID) -> DashboardListResponse:
    """The live list, from stored columns, never a definition (AC96)."""
    return DashboardListResponse(
        dashboards=[
            DashboardSummary(
                id=row.dashboard.id,
                title=row.dashboard.title,
                description=row.dashboard.description,
                owner=row.owner,
                panel_count=row.dashboard.panel_count,
                uses_picker=row.dashboard.uses_picker,
                panels_not_reading=row.dashboard.panels_not_reading,
                last_viewed_at=row.dashboard.last_viewed_at,
                created_at=row.dashboard.created_at,
                updated_at=row.dashboard.updated_at,
            )
            for row in await queries.list_live(db, org_id=org_id)
        ]
    )


async def readable_dashboard(
    db: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID, for_update: bool = False
) -> Dashboard:
    """The live dashboard, or the 404 an unknown id and another organization's share."""
    found = await queries.find_live(
        db, org_id=org_id, dashboard_id=dashboard_id, for_update=for_update
    )
    if found is None:
        raise DashboardNotFoundError(NO_SUCH_DASHBOARD)
    return found


async def readable_project(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    """The project a dashboard is shown against, or a 404 telling it from the dashboard's."""
    found = await project_queries.find_project(db, org_id=org_id, project_id=project_id)
    if found is None:
        raise ProjectNotFoundError(NO_SUCH_PROJECT)
    return found


async def get_dashboard(
    db: AsyncSession, *, org_id: uuid.UUID, dashboard_id: uuid.UUID
) -> DashboardResponse:
    return await _response(
        db, await readable_dashboard(db, org_id=org_id, dashboard_id=dashboard_id)
    )


def templates() -> DashboardTemplatesResponse:
    return DashboardTemplatesResponse(
        templates=[
            DashboardTemplate(id=template_id, name=name, document=document)
            for template_id, (name, document) in TEMPLATES.items()
        ]
    )


# ------------------------------------------------------------------ writing


async def create_dashboard(
    db: AsyncSession, *, identity: Identity, document: DashboardDocument, now: datetime
) -> DashboardResponse:
    check_size(document)
    compiled = compile_for_save(document.definition)
    await queries.hold_the_organizations_dashboards(db, org_id=identity.org_id)
    counted = await queries.counts(db, org_id=identity.org_id)
    if counted.live >= LIVE_DASHBOARDS_CAP:
        raise ConflictError(
            f"This organization holds {LIVE_DASHBOARDS_CAP:,} dashboards. Delete one first."
        )
    if counted.all >= ALL_DASHBOARDS_CAP:
        raise ConflictError(
            f"This organization holds {ALL_DASHBOARDS_CAP:,} dashboards, counting those deleted "
            "in the last 30 days. Delete one, or wait for deleted ones to expire."
        )
    await _refuse_a_taken_title(db, org_id=identity.org_id, title=document.title)

    dashboard = Dashboard(
        org_id=identity.org_id,
        title=document.title,
        description=document.description,
        definition=document.definition.model_dump(mode="json"),
        version=1,
        created_by=identity.user.id,
        updated_by=identity.user.id,
        panel_count=compiled.panel_count,
        service_names=compiled.service_names,
        uses_picker=compiled.uses_picker,
        created_at=now,
        updated_at=now,
    )
    db.add(dashboard)
    await _flush_refusing_a_taken_title(db, title=document.title)
    audit.record_dashboard_created(
        db,
        dashboard_id=dashboard.id,
        org_id=dashboard.org_id,
        actor_user_id=identity.user.id,
        facts=_facts(dashboard),
    )
    return await _response(db, dashboard)


async def replace_dashboard(
    db: AsyncSession,
    *,
    identity: Identity,
    dashboard_id: uuid.UUID,
    body: ReplaceDashboard,
    now: datetime,
) -> DashboardResponse:
    """Save over a dashboard, only from the version it was read at (FR19)."""
    check_size(body)
    dashboard = await readable_dashboard(
        db, org_id=identity.org_id, dashboard_id=dashboard_id, for_update=True
    )
    if body.version != dashboard.version:
        saved_by = await queries.person_name(
            db, org_id=dashboard.org_id, user_id=dashboard.updated_by
        )
        raise ConflictError(
            f"{saved_by or 'Someone'} saved this dashboard since you opened it. Reload it, "
            "or save yours as a copy.",
            saved_by=saved_by,
            saved_at=dashboard.updated_at.isoformat(),
            version=dashboard.version,
        )
    compiled = compile_for_save(body.definition)
    definition = body.definition.model_dump(mode="json")
    title_or_definition_changed = dashboard.title != body.title or canonical_json(
        dashboard.definition
    ) != canonical_json(definition)
    if not title_or_definition_changed and dashboard.description == body.description:
        # A save changing nothing makes no version and no entry (FR51, AC125).
        return await _response(db, dashboard)
    await _refuse_a_taken_title(
        db, org_id=identity.org_id, title=body.title, other_than=dashboard.id
    )

    before = _facts(dashboard)
    if title_or_definition_changed:
        db.add(
            DashboardVersion(
                dashboard_id=dashboard.id,
                org_id=dashboard.org_id,
                version=dashboard.version,
                title=dashboard.title,
                description=dashboard.description,
                definition=dashboard.definition,
                saved_by=dashboard.updated_by,
                saved_at=dashboard.updated_at,
            )
        )
    dashboard.title = body.title
    dashboard.description = body.description
    dashboard.definition = definition
    dashboard.version += 1
    dashboard.updated_by = identity.user.id
    dashboard.updated_at = now
    dashboard.panel_count = compiled.panel_count
    dashboard.service_names = compiled.service_names
    dashboard.uses_picker = compiled.uses_picker
    await _flush_refusing_a_taken_title(db, title=body.title)
    await queries.trim_versions(
        db,
        org_id=dashboard.org_id,
        dashboard_id=dashboard.id,
        keep_newest=VERSIONS_KEPT_NEWEST,
        older_than=now - VERSIONS_KEPT_FOR,
        ceiling=VERSIONS_CEILING,
    )
    audit.record_dashboard_updated(
        db,
        dashboard_id=dashboard.id,
        org_id=dashboard.org_id,
        actor_user_id=identity.user.id,
        facts=_facts(dashboard),
        before=before,
    )
    return await _response(db, dashboard)


async def delete_dashboard(
    db: AsyncSession, *, identity: Identity, dashboard_id: uuid.UUID, now: datetime
) -> None:
    """Hide a dashboard for 30 days, when the expiry sweep removes it (FR51)."""
    dashboard = await readable_dashboard(
        db, org_id=identity.org_id, dashboard_id=dashboard_id, for_update=True
    )
    dashboard.deleted_at = now
    audit.record_dashboard_deleted(
        db,
        dashboard_id=dashboard.id,
        org_id=dashboard.org_id,
        actor_user_id=identity.user.id,
        facts=_facts(dashboard),
    )


def stamp_opened(dashboard: Dashboard, *, identity: Identity, now: datetime) -> bool:
    """Move "last opened" on a batch read, at most hourly, never in a support view (FR37,
    NFR10). The row is already in hand, so this adds no read."""
    if identity.impersonation is not None:
        return False
    if dashboard.last_viewed_at is not None and now - dashboard.last_viewed_at < LAST_OPENED_EVERY:
        return False
    dashboard.last_viewed_at = now
    return True


# ------------------------------------------------------------------ context


async def context(
    db: AsyncSession,
    *,
    identity: Identity,
    dashboard_id: uuid.UUID,
    project_id: uuid.UUID,
    range_id: RangeId | None,
    from_time: datetime | None,
    to_time: datetime | None,
    self_hosted: bool,
    now: datetime,
) -> DashboardContext:
    """The Postgres half of the page: it answers whatever ClickHouse is doing (D24)."""
    await readable_dashboard(db, org_id=identity.org_id, dashboard_id=dashboard_id)
    project = await readable_project(db, org_id=identity.org_id, project_id=project_id)
    window_start, window_end = window_for(range_id, from_time, to_time, now=now)
    findings = await detection_queries.project_findings_in_window(
        db,
        org_id=identity.org_id,
        project_id=project.id,
        start=window_start,
        end=window_end,
        limit=detection_queries.PROJECT_FINDINGS_PAGE + 1,
    )
    status = await detection.project_status(
        db, org_id=identity.org_id, project_id=project.id, now=now
    )
    last_seen_at = await queries.project_last_seen(
        db, org_id=identity.org_id, project_id=project.id
    )
    return DashboardContext(
        project=ContextProject(
            id=project.id,
            name=project.name,
            timezone=project.timezone,
            retention=None if self_hosted else await _retention(db, identity=identity, now=now),
        ),
        window_start=window_start,
        window_end=window_end,
        detection=status,
        ever_reported=last_seen_at is not None,
        last_seen_at=last_seen_at,
        findings=[
            ContextFinding(
                id=row.id,
                scope=row.scope,
                service_name=row.service_name,
                severity=row.severity,
                title=row.title,
                started_at=row.started_at,
                closed_at=row.closed_at,
            )
            for row in findings[: detection_queries.PROJECT_FINDINGS_PAGE]
        ],
        findings_truncated=len(findings) > detection_queries.PROJECT_FINDINGS_PAGE,
        open_findings=OpenFindings(
            total=status.open_findings,
            by_service=await detection_queries.open_findings_by_service(
                db, org_id=identity.org_id, project_id=project.id
            ),
        ),
    )


async def _retention(db: AsyncSession, *, identity: Identity, now: datetime) -> PlanRetention:
    """What the organization's plan in force keeps, so "Your plan keeps 30 days" is true."""
    in_force = await usage_queries.plan_in_force(
        db, org_id=identity.org_id, paid_plan=identity.organization.plan, at=now
    )
    plan = await usage_queries.find_plan(db, slug=in_force.slug)
    if plan is None:
        # `organizations.plan` is a restricted foreign key: a missing row is a broken
        # database, not a plan to guess at.
        raise InternalError()
    return PlanRetention(raw_days=plan.raw_retention_days, derived_days=plan.derived_retention_days)


# ------------------------------------------------------------------ helpers


def _facts(dashboard: Dashboard) -> audit.DashboardFacts:
    stored = parse_stored(dashboard.definition)
    return audit.DashboardFacts(
        title=dashboard.title,
        panel_count=dashboard.panel_count,
        version=dashboard.version,
        definition_sha256=hashlib.sha256(canonical_json(dashboard.definition).encode()).hexdigest(),
        link_hosts=tuple(link_hosts(stored)),
    )


async def _refuse_a_taken_title(
    db: AsyncSession, *, org_id: uuid.UUID, title: str, other_than: uuid.UUID | None = None
) -> None:
    holder = await queries.live_title_holder(db, org_id=org_id, title=title, other_than=other_than)
    if holder is not None:
        raise _title_taken(holder.title, holder.id)


async def _flush_refusing_a_taken_title(db: AsyncSession, *, title: str) -> None:
    """The partial unique index is the guarantee; the check above only words it."""
    try:
        await db.flush()
    except IntegrityError as taken:
        if "uq_dashboards_live_title" not in str(taken.orig):
            raise
        raise _title_taken(title, None) from taken


def _title_taken(title: str, holder_id: uuid.UUID | None) -> ConflictError:
    return ConflictError(
        f"A dashboard named {title!r} exists. Choose another title, or open that one.",
        existing_dashboard_id=None if holder_id is None else str(holder_id),
    )


async def _response(db: AsyncSession, dashboard: Dashboard) -> DashboardResponse:
    return DashboardResponse(
        id=dashboard.id,
        title=dashboard.title,
        description=dashboard.description,
        definition=dashboard.definition,
        version=dashboard.version,
        owner=await queries.person_name(db, org_id=dashboard.org_id, user_id=dashboard.created_by),
        updated_by=await queries.person_name(
            db, org_id=dashboard.org_id, user_id=dashboard.updated_by
        ),
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )
