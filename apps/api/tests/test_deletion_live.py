"""Deleting one organization, against real Postgres.

**Why two tenants.** A deletion that takes too much is worse than one that takes
too little, and a single-tenant fixture cannot tell those apart: a `DELETE FROM
users` with no predicate passes it. So every table gets rows for two
organizations, one is deleted, and the other is counted before and after and
must be identical.

**Postgres only.** The request deletes the rows; the telemetry and the uploaded
files are the scheduler's nightly erasure, proved in
`apps/scheduler/tests/test_erasure_live.py` (ADR-014).

**What it leaves behind.** Nothing: it runs inside one transaction that is
always rolled back.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform import erasure
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.queries import load_identity
from nexovue_api.billing import BillingError, CancellationOutcome, ConfiguredProcessor
from nexovue_api.checkfiles.service import object_key as check_file_key
from nexovue_api.data.postgres.models import (
    AlertEvaluation,
    AlertRule,
    AlertRuleChannel,
    ApiToken,
    BaselineShift,
    BillingSubscription,
    BillingWebhookDelivery,
    Check,
    CheckFile,
    CheckSecret,
    CheckState,
    CheckStateChange,
    CheckVariable,
    DeployEvent,
    DetectionBaseline,
    DetectionProjectRun,
    DetectionState,
    ErrorGroupSeen,
    ErrorGroupWatermark,
    Finding,
    FindingIncident,
    Incident,
    Integration,
    Job,
    JobResult,
    MaintenanceWindow,
    MaintenanceWindowCheck,
    Notification,
    NotificationAttempt,
    NotificationChannel,
    Organization,
    PlanGrant,
    ServiceDetectionSettings,
    ServiceLastSeen,
    SessionRecord,
    SiteKey,
    SourceMap,
    SupportAccessGrant,
    UsageDaily,
    UsageDailyService,
    ViewAsTicketRecord,
    WebsitePayment,
)
from nexovue_api.errors.exceptions import ConflictError, DependencyUnavailableError
from nexovue_api.organizations import service
from nexovue_api.organizations.tables import (
    DELETED_DIRECTLY,
    EXCLUDED_TABLES,
    REMOVED_BY_CASCADE,
    TENANT_COLUMN,
    TOMBSTONED_TABLE,
)
from nexovue_api.signup.service import provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# Enough rows per table that "the other tenant is untouched" is a statement
# about a set rather than about one row.
ROWS_PER_TABLE: Final = 3
MAPS_PER_TENANT: Final = 2

PASSWORD: Final = "correct horse battery staple"
MAP_BYTES: Final = b'{"version":3,"sources":[],"names":[],"mappings":""}'

# The receiver's own read of the deletion flag, copied from the SELECT in
# `apps/receiver/internal/allowance/postgres.go`. Asserted verbatim so that "the
# console closed the account" and "the ingest door refuses it" are proven to be
# one fact rather than two beliefs.
RECEIVER_READS_DELETION: Final = text(
    "SELECT org.deleted_at IS NOT NULL FROM organizations org WHERE org.id = :org_id"
)

# One statement per table the deletion is responsible for, written out here and
# not taken from `queries.py`. A check that used the code under test to prove
# the code under test would agree with it when both are wrong. `project_first_traces`
# carries no `org_id`, so it is counted by the project id the fixture captured
# before anything was deleted — a join through `projects` would report zero once
# the projects had gone, hiding exactly the orphan this suite is looking for.
_COUNT_BY_ORG: Final[tuple[str, ...]] = (
    "billing_subscriptions",
    "billing_webhook_deliveries",
    "api_tokens",
    "website_payments",
    "plan_grants",
    "support_access_grants",
    "users",
    "projects",
    "api_keys",
    "site_keys",
    "checks",
    "check_states",
    "check_state_changes",
    "check_secrets",
    "check_variables",
    "check_files",
    "maintenance_windows",
    "maintenance_window_checks",
    "integrations",
    "deploy_events",
    "project_webhook_secrets",
    "source_maps",
    "usage_daily",
    "usage_daily_services",
    "audit_log",
    "notification_channels",
    "alert_rules",
    "alert_rule_channels",
    "alert_evaluations",
    "incidents",
    "notifications",
    "notification_attempts",
    "service_last_seen",
    "error_groups_seen",
    "error_group_watermarks",
    "findings",
    "finding_incidents",
    "detection_state",
    "detection_project_runs",
    "detection_baselines",
    "baseline_shifts",
    "service_detection_settings",
    "sessions",
    "view_as_tickets",
    "jobs",
    "job_results",
)

# What the fixture writes, per tenant, spelled out rather than counted at run
# time. Written out because it is the "before" half of the evidence: a fixture
# that quietly stopped writing to a table would make the deletion of that table
# unprovable while every assertion still passed. `users`, `projects`, `api_keys`
# and `project_webhook_secrets` are one each because `provision_tenant` makes
# one of each; `audit_log` is four because it records the organization, the
# minted key, the owners channel and the default alert rule — all under the
# tenant, so all deleted; the request's own entry is tenantless. Alert channels, rules
# and their links are one more than written, for the same reason: every tenant is
# created with an owners channel and a default rule sending to it (F21).
FIXTURE_ROWS: Final[dict[str, int]] = {
    "api_keys": 1,
    "billing_subscriptions": ROWS_PER_TABLE,
    "billing_webhook_deliveries": ROWS_PER_TABLE,
    # F36: one read token per organization.
    "api_tokens": 1,
    # F31: one per organization.
    "website_payments": 1,
    "plan_grants": 1,
    "support_access_grants": 1,
    # Signup's audit rows: one more for F6's second default rule.
    "audit_log": 5,
    "notification_channels": 1 + ROWS_PER_TABLE,
    # Signup's two default rules (F21 FR8, F6 FR29), and the fixture's.
    "alert_rules": 2 + ROWS_PER_TABLE,
    "alert_rule_channels": 2 + ROWS_PER_TABLE,
    "alert_evaluations": ROWS_PER_TABLE,
    "incidents": ROWS_PER_TABLE,
    # One per incident and one per finding, and the project's *detection stopped* (D24).
    "notifications": 2 * ROWS_PER_TABLE + 1,
    "notification_attempts": ROWS_PER_TABLE,
    # F6: a finding linked to each incident, and detection's per-service rows; one
    # project, so one run.
    "findings": ROWS_PER_TABLE,
    "finding_incidents": ROWS_PER_TABLE,
    "detection_state": ROWS_PER_TABLE,
    "detection_project_runs": 1,
    "detection_baselines": ROWS_PER_TABLE,
    "baseline_shifts": ROWS_PER_TABLE,
    "service_detection_settings": ROWS_PER_TABLE,
    "service_last_seen": ROWS_PER_TABLE,
    "error_groups_seen": ROWS_PER_TABLE,
    "error_group_watermarks": 1,
    "checks": ROWS_PER_TABLE,
    "check_states": ROWS_PER_TABLE,
    "check_state_changes": ROWS_PER_TABLE,
    "check_secrets": ROWS_PER_TABLE,
    "check_variables": ROWS_PER_TABLE,
    "check_files": ROWS_PER_TABLE,
    "maintenance_windows": ROWS_PER_TABLE,
    "maintenance_window_checks": ROWS_PER_TABLE,
    "deploy_events": ROWS_PER_TABLE,
    "integrations": ROWS_PER_TABLE,
    "organizations": 1,
    "project_first_traces": 1,
    "project_webhook_secrets": 1,
    "projects": 1,
    "site_keys": ROWS_PER_TABLE,
    "source_maps": MAPS_PER_TENANT,
    "usage_daily": ROWS_PER_TABLE,
    "usage_daily_services": ROWS_PER_TABLE,
    "users": 1,
    # ADR-020: what Redis held. One signed-in session and one support ticket for
    # the tenant's person; one queued job and one uncollected result.
    "sessions": 1,
    "view_as_tickets": 1,
    "jobs": 1,
    "job_results": 1,
}


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port),
            REACHABILITY_TIMEOUT_SECONDS,
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN, 5432),
    suite="the live organization deletion suite",
    remedy="Postgres is not answering — start it with `just dev`",
)


@dataclass
class Tenant:
    """One fixture organization, and the ids its rows are counted by."""

    org_id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    email: str


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session in a transaction that is always rolled back."""
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def two_tenants(db: AsyncSession) -> tuple[Tenant, Tenant]:
    """A tenant to delete and a tenant that must survive it.

    Fresh ids every run, so the suite can never collide with the seeded
    organization.
    """
    return await _build_tenant(db, "deleted"), await _build_tenant(db, "spared")


async def _build_tenant(db: AsyncSession, label: str) -> Tenant:
    """One organization with a row in every Postgres table a deletion touches."""
    email = f"{label}-{uuid.uuid4()}@example.test"
    provisioned = await provision_tenant(
        db, organization_name=f"Deletion {label}", email=email, password=PASSWORD
    )
    await db.flush()
    tenant = Tenant(
        org_id=provisioned.organization.id,
        user_id=provisioned.user.id,
        project_id=provisioned.project.id,
        email=email,
    )
    await _write_postgres_rows(db, tenant)
    return tenant


async def _write_postgres_rows(db: AsyncSession, tenant: Tenant) -> None:
    """Fill every table `provision_tenant` does not already touch."""
    day = datetime.now(UTC).date()
    now = datetime.now(UTC)
    for index in range(ROWS_PER_TABLE):
        check_id, window_id, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        db.add(
            Check(
                id=check_id,
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                name=f"check {index}",
                type="tcp",
                config={"type": "tcp", "host": "example.test", "port": 443},
                interval_seconds=60,
            )
        )
        db.add(
            MaintenanceWindow(
                id=window_id,
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                name=f"window {index}",
                starts_at=now,
                ends_at=now + timedelta(hours=1),
            )
        )
        await db.flush()
        key = check_file_key(tenant.org_id, file_id)
        db.add_all(
            [
                CheckState(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    check_id=check_id,
                    state="up",
                    since=now,
                    last_run_at=now,
                ),
                CheckStateChange(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    check_id=check_id,
                    to_state="up",
                    changed_at=now,
                ),
                CheckSecret(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    name=f"SECRET_{index}",
                    sealed_value=b"sealed",
                ),
                CheckVariable(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    name=f"variable_{index}",
                    value="value",
                ),
                CheckFile(
                    id=file_id,
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    check_id=check_id,
                    name="payload.bin",
                    content_type="application/octet-stream",
                    size_bytes=len(MAP_BYTES),
                ),
                MaintenanceWindowCheck(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    window_id=window_id,
                    check_id=check_id,
                ),
            ]
        )
        db.add_all(
            [
                SiteKey(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    name=f"site {index}",
                    public_key=f"nvp_live_{uuid.uuid4().hex[:22]}",
                    allowed_origins=["https://example.test"],
                ),
                Integration(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    kind="aws",
                    # `config` is NOT NULL in the database with no default, even
                    # though models.py declares one — migration 0006 dropped it.
                    config={},
                    status="pending",
                    external_id=uuid.uuid4(),
                ),
                DeployEvent(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    version=f"1.0.{index}",
                    service_name="checkout",
                    deployed_at=datetime.now(UTC),
                    source="manual",
                ),
                UsageDaily(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    day=day - timedelta(days=index),
                    spans=index,
                ),
                UsageDailyService(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    day=day - timedelta(days=index),
                    service_name=f"service-{index}",
                ),
                BillingSubscription(
                    org_id=tenant.org_id,
                    processor="test",
                    subscription_ref=f"sub-{uuid.uuid4()}",
                    plan_slug="free",
                    status="active",
                    services_committed=3,
                    currency="USD",
                ),
                BillingWebhookDelivery(
                    org_id=tenant.org_id,
                    processor="test",
                    delivery_id=f"evt-{uuid.uuid4()}",
                    kind="subscription.activated",
                    subscription_ref=f"sub-{uuid.uuid4()}",
                ),
            ]
        )
    # F31: one of each per organization — a payment is unique to it.
    # F36: one read token, whose daily counters go with it by cascade.
    db.add_all(
        [
            ApiToken(
                org_id=tenant.org_id,
                name="CI",
                token_prefix=f"nvt_live_{uuid.uuid4().hex[:11]}",
                token_hash=uuid.uuid4().hex * 2,
                expires_at=now + timedelta(days=90),
            ),
            WebsitePayment(
                org_id=tenant.org_id,
                razorpay_payment_id=f"pay_{uuid.uuid4().hex[:12]}",
                amount_minor=499_900,
                currency="INR",
            ),
            PlanGrant(
                org_id=tenant.org_id,
                plan_slug="pro",
                services=5,
                starts_at=now,
                ends_at=now + timedelta(days=30),
                reason="deletion test",
            ),
            SupportAccessGrant(
                org_id=tenant.org_id, granted_at=now, expires_at=now + timedelta(hours=72)
            ),
        ]
    )
    await db.flush()

    for index in range(ROWS_PER_TABLE):
        await _write_alert_rows(db, tenant, index, now)
    await _write_detection_project_rows(db, tenant, now)
    db.add_all(
        [
            *(
                ServiceLastSeen(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    service_name=f"service-{index}",
                    last_seen_at=now,
                )
                for index in range(ROWS_PER_TABLE)
            ),
            *(
                ErrorGroupSeen(
                    org_id=tenant.org_id,
                    project_id=tenant.project_id,
                    group_id=f"group-{index}",
                    first_seen_at=now,
                )
                for index in range(ROWS_PER_TABLE)
            ),
            ErrorGroupWatermark(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                received_through=now,
                baselined_at=now,
            ),
        ]
    )
    await db.flush()

    for index in range(MAPS_PER_TENANT):
        source_map_id = uuid.uuid4()
        key = f"{erasure.SOURCE_MAPS_ROOT}/{tenant.org_id}/{tenant.project_id}/{source_map_id}.map"
        db.add(
            SourceMap(
                id=source_map_id,
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                release="2026.09.08-1",
                minified_path=f"static/js/{index}.js",
                storage_key=key,
                size_bytes=len(MAP_BYTES),
            )
        )

    later = now + timedelta(hours=1)
    db.add_all(
        [
            SessionRecord(
                token_hash=uuid.uuid4().hex * 2,
                user_id=tenant.user_id,
                org_id=tenant.org_id,
                expires_at=later,
            ),
            ViewAsTicketRecord(
                token_hash=uuid.uuid4().hex * 2,
                user_id=tenant.user_id,
                org_id=tenant.org_id,
                operator_label="support",
                reason="deletion test",
                grant_id=uuid.uuid4(),
                impersonation_id=uuid.uuid4(),
                until=later,
                expires_at=later,
            ),
            Job(
                queue="light",
                task="deletion-test",
                kwargs={},
                org_id=tenant.org_id,
                max_attempts=1,
                soft_limit_seconds=1,
                hard_limit_seconds=2,
            ),
            JobResult(
                request_id=uuid.uuid4(),
                org_id=tenant.org_id,
                kind="check_run",
                payload={},
                expires_at=later,
            ),
        ]
    )

    # No model declares this table, so it is written the way the worker does.
    await db.execute(
        text("INSERT INTO project_first_traces (project_id, traces_stored) VALUES (:id, 1)"),
        {"id": tenant.project_id},
    )
    await db.flush()


async def _write_alert_rows(db: AsyncSession, tenant: Tenant, index: int, now: datetime) -> None:
    """One of everything F21 stores: a channel, a rule sending to it, an
    evaluation, an incident, its notification and one delivery attempt."""
    channel_id, rule_id, incident_id, batch_id = (uuid.uuid4() for _ in range(4))
    db.add_all(
        [
            NotificationChannel(
                id=channel_id,
                org_id=tenant.org_id,
                kind="email",
                name=f"channel {index}",
                config={"addresses": ["oncall@example.test"]},
            ),
            AlertRule(
                id=rule_id,
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                name=f"rule {index}",
                condition_kind="check_state",
                condition={"kind": "check_state", "states": {"down": "critical"}},
            ),
        ]
    )
    await db.flush()
    db.add_all(
        [
            AlertRuleChannel(org_id=tenant.org_id, rule_id=rule_id, channel_id=channel_id),
            AlertEvaluation(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                rule_id=rule_id,
                subject_key=f"service:checkout-{index}",
                phase="ok",
                phase_since=now,
                evaluated_at=now,
            ),
            Incident(
                id=incident_id,
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                rule_id=rule_id,
                rule_name=f"rule {index}",
                condition_kind="check_state",
                subject_key=f"check:{index}",
                subject_label=f"check {index}",
                severity="critical",
                opened_at=now,
            ),
        ]
    )
    await db.flush()
    db.add_all(
        [
            Notification(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                incident_id=incident_id,
                channel_id=channel_id,
                kind="opened",
                severity="critical",
                batch_id=batch_id,
            ),
            NotificationAttempt(
                org_id=tenant.org_id, channel_id=channel_id, batch_id=batch_id, outcome="delivered"
            ),
        ]
    )
    await db.flush()
    await _write_detection_rows(db, tenant, index, now, incident_id, channel_id)


async def _write_detection_rows(
    db: AsyncSession,
    tenant: Tenant,
    index: int,
    now: datetime,
    incident_id: uuid.UUID,
    channel_id: uuid.UUID,
) -> None:
    """One of everything F6 keeps per service: a finding acknowledged by the tenant's
    person, linked to the incident and told to the channel, and the service's state,
    normal, shift and settings."""
    finding_id, service = uuid.uuid4(), f"checkout-{index}"
    db.add(
        Finding(
            id=finding_id,
            org_id=tenant.org_id,
            project_id=tenant.project_id,
            scope="service",
            service_name=service,
            severity="critical",
            acknowledged_by=tenant.user_id,
            acknowledged_at=now,
            title=f"{service} is 4x slower than normal",
            started_at=now,
        )
    )
    await db.flush()
    db.add_all(
        [
            FindingIncident(org_id=tenant.org_id, finding_id=finding_id, incident_id=incident_id),
            Notification(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                finding_id=finding_id,
                channel_id=channel_id,
                kind="opened",
                severity="critical",
            ),
            DetectionState(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                service_name=service,
                last_judged_minute=now,
            ),
            DetectionBaseline(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                service_name=service,
                signal="slower",
                minute=now,
                baseline={"mode": "weekly"},
            ),
            BaselineShift(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                service_name=service,
                signal="slower",
                from_minute=now,
                ratio=1.6,
                reason="new_normal",
            ),
            ServiceDetectionSettings(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                service_name=service,
                muted_until=now + timedelta(days=1),
                muted_by=tenant.user_id,
            ),
        ]
    )
    await db.flush()


async def _write_detection_project_rows(db: AsyncSession, tenant: Tenant, now: datetime) -> None:
    """The project's last detection run, and the owners told it stopped (D24): a
    notification whose subject is the project alone."""
    owners = (
        await db.execute(
            select(NotificationChannel.id).where(
                NotificationChannel.org_id == tenant.org_id, NotificationChannel.kind == "owners"
            )
        )
    ).scalar_one()
    db.add_all(
        [
            DetectionProjectRun(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                last_completed_at=now,
                stopped_reported_at=now,
            ),
            Notification(
                org_id=tenant.org_id,
                project_id=tenant.project_id,
                channel_id=owners,
                kind="stopped",
                severity="critical",
            ),
        ]
    )
    await db.flush()


async def _row_counts(db: AsyncSession, tenant: Tenant) -> dict[str, int]:
    """Rows per Postgres table for one tenant, counted outside the deletion's code."""
    counts: dict[str, int] = {}
    for table in _COUNT_BY_ORG:
        counts[table] = await _count(
            db, f"SELECT count(*) FROM {table} WHERE org_id = :org_id", {"org_id": tenant.org_id}
        )
    counts["project_first_traces"] = await _count(
        db,
        "SELECT count(*) FROM project_first_traces WHERE project_id = :project_id",
        {"project_id": tenant.project_id},
    )
    counts[TOMBSTONED_TABLE] = await _count(
        db, "SELECT count(*) FROM organizations WHERE id = :org_id", {"org_id": tenant.org_id}
    )
    return counts


async def _count(db: AsyncSession, sql: str, parameters: dict[str, Any]) -> int:
    return int((await db.execute(text(sql), parameters)).scalar_one())


class _Processor:
    """The fixture's arrangements were taken through a processor named "test";
    this stands in for it and records every cancellation it is asked for."""

    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.ended_now: list[str] = []

    async def cancel(
        self, *, subscription_ref: str, immediately: bool = False
    ) -> CancellationOutcome:
        if not self.reachable:
            raise BillingError("the processor is not answering")
        if immediately:
            self.ended_now.append(subscription_ref)
        return CancellationOutcome.REQUESTED


def _payments(processor: _Processor | None = None) -> ConfiguredProcessor:
    return ConfiguredProcessor(
        name="test",
        processor=cast(Any, processor or _Processor()),
        processor_currencies=frozenset(),
    )


async def _delete(db: AsyncSession, tenant: Tenant) -> None:
    """The endpoint's one transaction, as the route drives it."""
    await service.request_deletion(db, org_id=tenant.org_id, payments=_payments())
    await db.flush()


async def test_the_fixture_gives_both_tenants_the_same_rows(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """A deletion test over empty tables passes without deleting anything."""
    deleted, spared = two_tenants
    before_deleted = await _row_counts(db, deleted)
    before_spared = await _row_counts(db, spared)

    assert before_deleted == FIXTURE_ROWS, (
        f"the fixture no longer writes what this suite measures: {before_deleted}"
    )
    assert before_spared == FIXTURE_ROWS, (
        f"the two tenants were not given the same rows: {before_deleted} vs {before_spared}"
    )


async def test_deleting_an_organization_empties_every_table_it_owned(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """The request alone, before any erasure has run (founder decision 2026-10-11).

    `organizations` is expected to hold its one row afterwards: the tombstone is
    the deletion's record of itself, and
    `test_the_tombstone_keeps_no_words_the_customer_wrote` proves it carries
    nothing of the customer's.
    """
    deleted, _ = two_tenants
    assert await _row_counts(db, deleted) == FIXTURE_ROWS

    await _delete(db, deleted)

    after = await _row_counts(db, deleted)
    assert after == dict.fromkeys(FIXTURE_ROWS, 0) | {TOMBSTONED_TABLE: 1}, (
        f"the request did not leave exactly the tombstone: {after}"
    )


async def test_the_owner_can_sign_up_again_at_once(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """An email is unique across nexovue, so a person row left for the night
    would refuse the same person's new signup until then."""
    deleted, _ = two_tenants
    await _delete(db, deleted)

    again = await provision_tenant(
        db, organization_name="Deletion again", email=deleted.email, password=PASSWORD
    )
    await db.flush()

    assert again.organization.id != deleted.org_id


async def test_deleting_an_organization_leaves_the_other_untouched(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """Every statement filters one tenant; only that tenant may go."""
    deleted, spared = two_tenants
    before = await _row_counts(db, spared)
    assert before == FIXTURE_ROWS

    await _delete(db, deleted)

    after = await _row_counts(db, spared)
    assert after == before, (
        f"deleting {deleted.org_id} changed rows belonging to {spared.org_id}: "
        f"before={before} after={after}"
    )


@pytest.mark.parametrize("omitted", sorted(DELETED_DIRECTLY))
async def test_a_table_left_out_of_the_deletion_is_named_rather_than_missed(
    db: AsyncSession,
    two_tenants: tuple[Tenant, Tenant],
    monkeypatch: pytest.MonkeyPatch,
    omitted: str,
) -> None:
    """The negative control, kept rather than run once by hand.

    `service.DELETED_DIRECTLY` drives the deletion and the counts above are
    written separately, so dropping a table from the first must be reported by
    the second, naming it — not pass quietly, which is the defect this suite
    exists to prevent. Each of the three also takes a set of cascades with it,
    which is why `projects` leaves ten tables behind rather than one.
    """
    deleted, _ = two_tenants
    before = await _row_counts(db, deleted)
    monkeypatch.setattr(
        "nexovue_api.organizations.service.DELETED_DIRECTLY",
        tuple(table for table in DELETED_DIRECTLY if table != omitted),
    )

    await _delete(db, deleted)

    after = await _row_counts(db, deleted)
    left_behind = {
        table: count for table, count in after.items() if count and table != TOMBSTONED_TABLE
    }
    assert omitted in left_behind, (
        f"dropping {omitted} from the deletion changed nothing the counts could see; "
        f"before={before} after={after}"
    )


async def test_the_request_alone_ends_every_session_and_closes_the_door(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """The customer-visible stop is the first transaction, not the erasure.

    Both halves are read through the code that actually enforces them:
    `load_identity`, which every authenticated request goes through, and the
    receiver's own SELECT over `organizations`.
    """
    deleted, spared = two_tenants
    assert await load_identity(db, user_id=deleted.user_id, org_id=deleted.org_id) is not None

    await _delete(db, deleted)

    assert await load_identity(db, user_id=deleted.user_id, org_id=deleted.org_id) is None
    refused = await db.execute(RECEIVER_READS_DELETION, {"org_id": deleted.org_id})
    assert refused.scalar_one() is True
    still_open = await db.execute(RECEIVER_READS_DELETION, {"org_id": spared.org_id})
    assert still_open.scalar_one() is False
    assert await load_identity(db, user_id=spared.user_id, org_id=spared.org_id) is not None


async def test_the_tombstone_keeps_no_words_the_customer_wrote(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """What survives is an id, a plan and two timestamps."""
    deleted, _ = two_tenants
    original = await db.execute(
        text("SELECT slug, name FROM organizations WHERE id = :org_id"),
        {"org_id": deleted.org_id},
    )
    slug, name = original.one()

    await _delete(db, deleted)

    scrubbed = await db.execute(
        text("SELECT slug, name FROM organizations WHERE id = :org_id"),
        {"org_id": deleted.org_id},
    )
    new_slug, new_name = scrubbed.one()
    assert new_slug != slug and new_name != name
    assert slug not in new_slug and name not in new_name


async def test_closing_an_organization_stops_every_charge_first(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """Blocker 20. Closing deleted the subscription rows and never told the
    processor, which kept debiting with no record left to refund from."""
    deleted, spared = two_tenants
    live = list(
        await db.scalars(
            select(BillingSubscription.subscription_ref).where(
                BillingSubscription.org_id == deleted.org_id
            )
        )
    )
    processor = _Processor()

    await service.request_deletion(db, org_id=deleted.org_id, payments=_payments(processor))

    assert live and sorted(processor.ended_now) == sorted(live)
    spared_live = await db.scalar(
        select(func.count()).where(
            BillingSubscription.org_id == spared.org_id, BillingSubscription.status == "active"
        )
    )
    assert spared_live == ROWS_PER_TABLE, "another organization's subscriptions were touched"


async def test_an_organization_whose_charges_cannot_be_stopped_is_not_closed(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """A retry is safe; a debit after the records are gone is not."""
    deleted, _ = two_tenants

    with pytest.raises(DependencyUnavailableError):
        await service.request_deletion(
            db, org_id=deleted.org_id, payments=_payments(_Processor(reachable=False))
        )
    with pytest.raises(DependencyUnavailableError):
        await service.request_deletion(db, org_id=deleted.org_id, payments=None)

    organization = await db.get(Organization, deleted.org_id)
    assert organization is not None and organization.deleted_at is None


async def test_a_second_deletion_request_is_refused(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """The first `deleted_at` is when the erasure became owed; nothing may move it."""
    deleted, _ = two_tenants
    accepted = await service.request_deletion(db, org_id=deleted.org_id, payments=_payments())
    await db.flush()
    with pytest.raises(ConflictError):
        await service.request_deletion(db, org_id=deleted.org_id, payments=_payments())
    assert accepted.organization_id == deleted.org_id


async def test_the_request_records_its_obligation_without_naming_anyone(
    db: AsyncSession, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """The night finds the obligation as a tombstone with no certificate. The
    request's own entry outlives the rows it deleted, so it names no person."""
    deleted, _ = two_tenants
    target = erasure.organization_target(deleted.org_id)

    await _delete(db, deleted)

    entries = (
        await db.execute(
            text(
                "SELECT org_id, actor_user_id, actor_label, action FROM audit_log "
                "WHERE target = :target"
            ),
            {"target": target},
        )
    ).all()
    assert [tuple(entry) for entry in entries] == [
        (None, None, None, erasure.DELETION_REQUESTED_ACTION)
    ]


async def test_the_database_holds_no_table_this_deletion_has_not_classified(
    db: AsyncSession,
) -> None:
    """The inventory against the live catalog, not against a list somebody kept.

    A table added next month fails here the day it is created, rather than
    leaving rows behind the first time somebody is erased.
    """
    result = await db.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    )
    in_the_database = {str(row[0]) for row in result}

    classified = (
        set(DELETED_DIRECTLY) | set(REMOVED_BY_CASCADE) | set(EXCLUDED_TABLES) | {TOMBSTONED_TABLE}
    )
    unclassified = sorted(in_the_database - classified)
    assert not unclassified, (
        f"the database holds tables the deletion neither erases nor records a reason "
        f"for leaving: {unclassified}"
    )
    missing = sorted(classified - in_the_database)
    assert not missing, f"the deletion names tables the database does not have: {missing}"


async def test_every_table_carrying_a_tenant_is_erased_rather_than_excluded(
    db: AsyncSession,
) -> None:
    """A table with `org_id` may never be on the excluded list.

    The excluded list is the only place this suite can be weakened, so the one
    thing that must not be excludable is a table that names a tenant.
    """
    result = await db.execute(
        text(
            "SELECT c.table_name FROM information_schema.columns c "
            "JOIN information_schema.tables t "
            "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
            "  AND c.column_name = :tenant_column"
        ),
        {"tenant_column": TENANT_COLUMN},
    )
    tenant_scoped = {str(row[0]) for row in result} - {TOMBSTONED_TABLE}

    unreached = sorted(tenant_scoped - set(DELETED_DIRECTLY) - set(REMOVED_BY_CASCADE))
    assert not unreached, (
        f"these tables carry {TENANT_COLUMN} and the deletion does not reach them: {unreached}"
    )


async def test_every_cascade_the_inventory_relies_on_is_really_a_cascade(
    db: AsyncSession,
) -> None:
    """A cascade that is `SET NULL`, or nullable, leaves rows behind silently.

    `REMOVED_BY_CASCADE` is the only part of the deletion with no statement of
    its own, so it is the part that has to be held against the catalog: the
    foreign key must be `ON DELETE CASCADE` *and* NOT NULL, or a row whose
    parent column was never set would survive its tenant.
    """
    result = await db.execute(
        text(
            "SELECT c.conrelid::regclass::text, c.confrelid::regclass::text, "
            "       c.confdeltype::text, bool_or(a.attnotnull) "
            "FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.contype = 'f' "
            "GROUP BY c.oid, c.conrelid, c.confrelid, c.confdeltype"
        )
    )
    cascades = {
        (str(child), str(parent))
        for child, parent, on_delete, not_null in result
        if on_delete == "c" and not_null
    }

    broken = sorted(
        f"{child} -> {parent}"
        for child, parent in REMOVED_BY_CASCADE.items()
        if (child, parent) not in cascades
    )
    assert not broken, (
        f"the deletion relies on these cascades and the database does not have them "
        f"as NOT NULL ON DELETE CASCADE: {broken}"
    )
