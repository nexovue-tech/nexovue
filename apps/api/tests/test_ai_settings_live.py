"""AI settings in the API, against real Postgres (F3 plan T4.5; AC30 to AC36, AC39's preview and
opt-out cases, AC40, AC72, AC102). Every test runs in a transaction that is rolled back.

The API never calls a provider: a test is a job on the `insight` queue, read here as the row
it would have added. The suite searches every response and audit row for the key.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from nexovue_platform.ai.check_prompt import CHECK_PROMPT_VERSION, CHECK_REQUEST
from nexovue_platform.ai.connection import AI_CONNECTION_PURPOSE, key_owner
from nexovue_platform.ai.explain_prompt import PROMPT_VERSION, SAMPLE_MODEL_INPUT, explain_request
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.jobs import AI_CHECK_RESULT_KIND, CHECK_AI_CONNECTION_TASK
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, generate_key, keyring_from
from nexovue_platform.email import EmailError, Message
from nexovue_platform.email.sender import Delivery
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from nexovue_platform.import_walk import direct_imports_matching, package_modules
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.ai import service
from nexovue_api.ai.schemas import SaveConnection
from nexovue_api.data.postgres.models import (
    AIConnection,
    AIRun,
    AuditLogEntry,
    FindingExplanation,
    Project,
)
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
KEYS: Final = ConfiguredKeys(generate_key())
KEY: Final = "sk-ant-test-0f1e2d3c4b5a"
NEW_KEY: Final = "sk-ant-test-replacement-99"
# A pinned clock: nothing here depends on the day it runs.
NOW: Final = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
CLAUDE: Final = "https://api.anthropic.com"
GATEWAY: Final = "https://gateway.example.com/team-secret-path/v1"
SELF_HOSTED: Final = service.Availability(
    deployment="self-hosted", keys=KEYS, insight_replicas=1, app_base_url="https://app.example"
)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_ai_settings_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            # The suite runs where `setup` made the AI account; it says so first (AC71).
            exists = await session.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexovue_insight')")
            )
            assert exists, "run `nexovue setup grants` (or the insight suites) first"
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="AI settings",
        email=f"ai-settings-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


@dataclass
class Mail:
    """Records each message; `refuse` makes every send fail, as a provider can."""

    refuse: bool = False
    sent: list[tuple[str, Message, Mapping[str, Any]]] = field(default_factory=list)

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> Delivery:
        del idempotency_key
        if self.refuse:
            raise EmailError("refused")
        self.sent.append((to, message, data))
        return Delivery(provider_message_id="m-1")


def _actor(tenant: ProvisionedTenant, user_id: uuid.UUID | None = None) -> service.Actor:
    return service.Actor(
        org_id=tenant.organization.id,
        user_id=user_id or tenant.user.id,
        organization_name=tenant.organization.name,
    )


def _request(**changes: Any) -> SaveConnection:
    values: dict[str, Any] = {
        "wire_format": "anthropic_messages",
        "base_url": CLAUDE,
        "model": "claude-haiku-5-5",
        "key": KEY,
        "monthly_token_limit": 2_000_000,
        "price_input": Decimal("1.0"),
        "price_output": Decimal("5.0"),
    }
    return SaveConnection(**(values | changes))


async def _save(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    mail: Mail | None = None,
    availability: service.Availability = SELF_HOSTED,
    **changes: Any,
) -> Any:
    return await service.save_connection(
        db,
        actor=_actor(tenant),
        request=_request(**changes),
        availability=availability,
        email=mail,
    )


async def _row(db: AsyncSession, tenant: ProvisionedTenant) -> AIConnection:
    row = await db.scalar(select(AIConnection).where(AIConnection.org_id == tenant.organization.id))
    assert row is not None
    await db.refresh(row)
    return row


async def _audit(db: AsyncSession, tenant: ProvisionedTenant) -> list[AuditLogEntry]:
    return list(
        await db.scalars(
            select(AuditLogEntry)
            .where(
                AuditLogEntry.org_id == tenant.organization.id,
                AuditLogEntry.action.like("ai.%"),
            )
            .order_by(AuditLogEntry.at, AuditLogEntry.id)
        )
    )


async def _pass_the_test(db: AsyncSession, tenant: ProvisionedTenant) -> None:
    """What the check task records for a pass (`insight/store.py`), for the saved values."""
    row = await _row(db, tenant)
    row.checked_ok, row.checked_at = True, NOW
    row.checked_fingerprint = service._check_fingerprint(row)
    await db.flush()


async def _consent(db: AsyncSession, tenant: ProvisionedTenant) -> Any:
    view = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    return await service.consent(
        db,
        actor=_actor(tenant),
        fingerprint=view.consent_fingerprint or "",
        schema_version=SCHEMA_VERSION,
        availability=SELF_HOSTED,
        now=NOW,
    )


async def _all_on(db: AsyncSession, tenant: ProvisionedTenant) -> None:
    await _save(db, tenant)
    await _pass_the_test(db, tenant)
    await _consent(db, tenant)
    await service.switch(db, actor=_actor(tenant), on=True, availability=SELF_HOSTED)


def _holds_no_key(document: Any) -> None:
    text_form = json.dumps(document, default=str)
    assert KEY not in text_form and NEW_KEY not in text_form


# ------------------------------------------------------------------ off by default


async def test_ai_is_off_for_a_new_organization_and_says_what_is_missing(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC30: off, nothing set up, and no run can start."""
    view = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    assert view.on is False and view.connection is None
    assert view.missing == ["connection"]
    assert view.formats_offered == [], "no format has a dated real call yet (AC77)"
    with pytest.raises(ConflictError):
        await service.switch(db, actor=_actor(tenant), on=True, availability=SELF_HOSTED)


async def test_a_person_without_manage_ai_reads_on_or_off_and_nothing_else(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC34: a viewer reading `GET /ai/connection` gets status only."""
    await _all_on(db, tenant)
    view = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=False, availability=SELF_HOSTED
    )
    shown = view.model_dump(exclude_defaults=True)
    assert shown == {"on": True, "full": False}
    assert CLAUDE not in json.dumps(view.model_dump(mode="json"))


# ------------------------------------------------------------------- saving


async def test_a_saved_key_is_sealed_for_its_address_and_never_returned(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC34: never in a response or an audit row; sealed so it opens only for this address."""
    view = await _save(db, tenant)
    row = await _row(db, tenant)

    _holds_no_key(view.model_dump(mode="json"))
    _holds_no_key([entry.detail for entry in await _audit(db, tenant)])
    assert view.connection.has_key is True
    box = SealedBox(keyring_from(KEYS.current), purpose=AI_CONNECTION_PURPOSE)
    owner = key_owner(str(row.org_id), str(row.id), CLAUDE)
    assert row.sealed_key is not None and box.open(row.sealed_key, owner=owner) == KEY
    [created] = await _audit(db, tenant)
    assert created.action == "ai.connection_changed"
    assert created.detail == {"created": True, "old_host": None, "new_host": CLAUDE}


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "http://api.anthropic.com"},
        {"base_url": "https://user:pw@api.anthropic.com"},
        {"base_url": "https://api.anthropic.com/v1?x=1"},
        {"base_url": "https://api.anthropic.com/v1#x"},
        {"base_url": "http://localhost:11434"},
        {"model": "claude/../../admin"},
        {"model": "two words"},
        {"key": "sk-1\r\nX-Injected: 1"},
    ],
)
async def test_a_value_fr23_does_not_allow_is_refused_on_save(
    db: AsyncSession, tenant: ProvisionedTenant, changes: dict[str, Any]
) -> None:
    with pytest.raises(InvalidRequestError):
        await _save(db, tenant, **changes)
    assert (
        await db.scalar(select(AIConnection).where(AIConnection.org_id == tenant.organization.id))
        is None
    )


async def test_a_new_address_with_no_new_key_erases_the_key_ends_consent_and_turns_off(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR23, AC33, AC72, AC102: in one transaction, with audit rows naming both hosts, and
    every owner emailed with no key."""
    await _all_on(db, tenant)
    mail = Mail()

    await _save(db, tenant, mail, base_url=GATEWAY, key=None)

    row = await _row(db, tenant)
    assert (row.sealed_key, row.key_id) == (None, None)
    assert row.enabled is False and row.off_reason == "consent_ended"
    assert row.consented_at is None and row.consented_fingerprint is None
    entries = {entry.action: entry.detail for entry in await _audit(db, tenant)}
    hosts = {"old_host": CLAUDE, "new_host": "https://gateway.example.com"}
    assert entries["ai.consent_ended"] == hosts
    assert entries["ai.connection_changed"] == {"fields": ["base_url"], **hosts}
    assert entries["ai.turned_off"] == {"by": "change"}
    _holds_no_key(entries)
    assert "team-secret-path" not in json.dumps(entries), "a path may hold a secret"
    [(to, message, data)] = mail.sent
    assert to == tenant.user.email and message is Message.AI_CONNECTION_CHANGED
    assert (data["old_host"], data["new_host"]) == (CLAUDE, "https://gateway.example.com")
    _holds_no_key(data)


async def test_a_new_key_ends_consent(db: AsyncSession, tenant: ProvisionedTenant) -> None:
    """AC33: the key is in what an owner agreed to."""
    await _all_on(db, tenant)
    before = (await _row(db, tenant)).key_id

    await _save(db, tenant, Mail(), key=NEW_KEY)

    row = await _row(db, tenant)
    assert row.key_id != before and row.enabled is False and row.consented_at is None
    actions = [entry.action for entry in await _audit(db, tenant)]
    assert "ai.key_replaced" in actions and "ai.consent_ended" in actions


async def test_a_new_model_keeps_consent_turns_off_until_a_test_and_clears_the_price(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR26, AC33, AC102: owners are told; a refused send does not undo the change."""
    await _all_on(db, tenant)

    await _save(db, tenant, Mail(refuse=True), model="claude-sonnet-5-5", key=None)

    row = await _row(db, tenant)
    assert row.model == "claude-sonnet-5-5" and row.sealed_key is not None
    assert row.consented_at is not None and row.enabled is False and row.off_reason == "person"
    assert (row.price_input, row.price_output) == (None, None)
    view = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    assert view.connection is not None and view.connection.model_changed is True
    assert view.missing == ["test"]
    with pytest.raises(ConflictError) as refused:
        await service.switch(db, actor=_actor(tenant), on=True, availability=SELF_HOSTED)
    assert refused.value.extensions["missing"] == ["test"]


async def test_a_model_change_emails_each_owner(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    await _all_on(db, tenant)
    mail = Mail()
    await _save(db, tenant, mail, model="claude-sonnet-5-5", key=None)
    [(_, message, data)] = mail.sent
    assert message is Message.AI_CONNECTION_CHANGED and "claude-sonnet-5-5" in data["what"]


# ------------------------------------------------------------- the switch


async def test_the_switch_names_everything_missing(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC31: no passing test, no limit, no consent: each named."""
    await _save(db, tenant, monthly_token_limit=None)
    with pytest.raises(ConflictError) as refused:
        await service.switch(db, actor=_actor(tenant), on=True, availability=SELF_HOSTED)
    assert refused.value.extensions["missing"] == ["test", "limit", "consent"]
    assert "a monthly token limit" in refused.value.detail


async def test_a_pass_for_old_values_does_not_turn_it_on(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC31: the test's fingerprint covers what it tested; a later save is untested."""
    await _save(db, tenant)
    await _pass_the_test(db, tenant)
    await _consent(db, tenant)
    await _save(db, tenant, Mail(), call_seconds=120, model="claude-opus-5-5", key=None)
    with pytest.raises(ConflictError) as refused:
        await service.switch(db, actor=_actor(tenant), on=True, availability=SELF_HOSTED)
    assert refused.value.extensions["missing"] == ["test"]


async def test_with_everything_done_it_turns_on_and_off_audited(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    await _all_on(db, tenant)
    assert (await _row(db, tenant)).enabled is True
    await service.switch(db, actor=_actor(tenant), on=False, availability=SELF_HOSTED)
    row = await _row(db, tenant)
    assert row.enabled is False and row.off_reason == "person"
    actions = [entry.action for entry in await _audit(db, tenant)]
    assert actions.index("ai.turned_on") < actions.index("ai.turned_off")


async def test_without_the_ai_container_the_switch_and_test_are_refused(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC103's screen half: with `SCHEDULER_INSIGHT_REPLICAS=0` the API says so."""
    stopped = service.Availability(
        deployment="self-hosted", keys=KEYS, insight_replicas=0, app_base_url=""
    )
    await _save(db, tenant)
    view = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=stopped
    )
    assert view.container_running is False and "container" in view.missing
    with pytest.raises(ConflictError):
        await service.start_check(db, actor=_actor(tenant), availability=stopped)


# ------------------------------------------------------------------ consent


async def test_consent_with_a_fingerprint_that_moved_is_refused(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC33: a key or address swapped between the owner's read and click is refused (409)."""
    await _save(db, tenant)
    read = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    await _save(db, tenant, Mail(), key=NEW_KEY)
    with pytest.raises(ConflictError):
        await service.consent(
            db,
            actor=_actor(tenant),
            fingerprint=read.consent_fingerprint or "",
            schema_version=SCHEMA_VERSION,
            availability=SELF_HOSTED,
            now=NOW,
        )
    with pytest.raises(ConflictError):
        await service.consent(
            db,
            actor=_actor(tenant),
            fingerprint=read.consent_fingerprint or "",
            schema_version=SCHEMA_VERSION + 1,
            availability=SELF_HOSTED,
            now=NOW,
        )


async def test_consent_records_who_when_what_and_the_host(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    await _save(db, tenant)
    await _consent(db, tenant)
    row = await _row(db, tenant)
    assert row.consented_by == tenant.user.id and row.consented_at == NOW
    assert row.consented_schema == SCHEMA_VERSION and row.consented_base_url == CLAUDE
    agreed = [e for e in await _audit(db, tenant) if e.action == "ai.disclosure_agreed"]
    assert agreed[0].detail == {"old_host": None, "new_host": CLAUDE, "schema": SCHEMA_VERSION}


async def test_consent_to_an_older_facts_schema_is_not_consent(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC33: a release widening what may be sent ends consent: an older schema is not current."""
    await _all_on(db, tenant)
    row = await _row(db, tenant)
    row.consented_schema = SCHEMA_VERSION - 1
    await db.flush()
    view = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    assert view.on is False and "consent" in view.missing


async def test_an_owner_who_left_still_consented_and_the_screen_says_so(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR26: consent stands when its owner leaves."""
    await _all_on(db, tenant)
    await db.execute(
        text("UPDATE users SET removed_at = :at WHERE id = :id"), {"at": NOW, "id": tenant.user.id}
    )
    view = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    assert view.on is True
    assert view.connection is not None and view.connection.consent is not None
    assert view.connection.consent.by_left is True and view.connection.consent.current is True


# ------------------------------------------------------------------ delete


async def test_removing_the_connection_erases_it_ends_consent_and_is_audited(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC34, AC72, AC102."""
    await _all_on(db, tenant)
    mail = Mail()
    await service.delete_connection(db, actor=_actor(tenant), availability=SELF_HOSTED, email=mail)
    assert (
        await db.scalar(select(AIConnection).where(AIConnection.org_id == tenant.organization.id))
        is None
    )
    entries = {entry.action: entry.detail for entry in await _audit(db, tenant)}
    assert entries["ai.connection_deleted"] == {"old_host": CLAUDE, "new_host": None}
    assert "ai.turned_off" in entries
    assert len(mail.sent) == 1


# --------------------------------------------------------------------- test


async def test_a_test_is_a_job_on_the_insight_queue_with_ids_only(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC35: the API never calls a provider; the job's arguments hold no finding data."""
    await _save(db, tenant)
    accepted = await service.start_check(db, actor=_actor(tenant), availability=SELF_HOSTED)
    [job] = (
        await db.execute(
            text("SELECT task, queue, kwargs FROM jobs WHERE org_id = :org"),
            {"org": tenant.organization.id},
        )
    ).all()
    assert (job.task, job.queue) == (CHECK_AI_CONNECTION_TASK, "insight")
    assert job.kwargs == {
        "org_id": str(tenant.organization.id),
        "request_id": str(accepted.request_id),
        "requested_by": str(tenant.user.id),
    }
    tested = [e for e in await _audit(db, tenant) if e.action == "ai.connection_tested"]
    assert tested[0].detail == {"old_host": CLAUDE, "new_host": CLAUDE}
    pending = await service.check_result(
        db, org_id=tenant.organization.id, request_id=accepted.request_id
    )
    assert pending.pending is True


async def test_another_organization_s_test_is_not_found(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC35: another organization's `request_id` gets 404, while queued and once answered."""
    other = await provision_tenant(
        db,
        organization_name="Other",
        email=f"other-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    await _save(db, tenant)
    accepted = await service.start_check(db, actor=_actor(tenant), availability=SELF_HOSTED)
    with pytest.raises(NotFoundError):
        await service.check_result(db, org_id=other.organization.id, request_id=accepted.request_id)
    await db.execute(
        text(
            "INSERT INTO job_results (request_id, org_id, kind, payload, expires_at) "
            "VALUES (:id, :org, :kind, CAST(:payload AS jsonb), :expires)"
        ),
        {
            "id": accepted.request_id,
            "org": tenant.organization.id,
            "kind": AI_CHECK_RESULT_KIND,
            "payload": json.dumps({"ok": False, "reason": "not_found", "input_tokens": 3}),
            "expires": NOW + timedelta(days=3650),
        },
    )
    answered = await service.check_result(
        db, org_id=tenant.organization.id, request_id=accepted.request_id
    )
    assert (answered.pending, answered.ok, answered.reason) == (False, False, "not_found")
    with pytest.raises(NotFoundError):
        await service.check_result(db, org_id=other.organization.id, request_id=accepted.request_id)


def test_no_api_module_imports_the_transport_or_the_scheduler() -> None:
    """AC35: the API holds the pure formats and never the means to call a provider."""
    modules = package_modules("nexovue_api")
    assert "nexovue_api.ai.service" in modules
    assert direct_imports_matching(modules, frozenset({"nexovue_scheduler", "pycurl"})) == []


# ------------------------------------------------------------------ preview


async def _finding(
    db: AsyncSession, tenant: ProvisionedTenant, project_id: uuid.UUID
) -> FindingRow:
    row = FindingRow(
        org_id=tenant.organization.id,
        project_id=project_id,
        scope="service",
        service_name="checkout",
        severity="warning",
        state="open",
        title="checkout is slower than normal",
        signals=[],
        started_at=NOW - timedelta(minutes=30),
    )
    db.add(row)
    await db.flush()
    return row


async def _explanation(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    project_id: uuid.UUID,
    *,
    read_at: datetime,
    model_input: dict[str, Any] | None = None,
    wording_state: str = "off",
) -> FindingExplanation:
    finding = await _finding(db, tenant, project_id)
    row = FindingExplanation(
        org_id=tenant.organization.id,
        project_id=project_id,
        finding_id=finding.id,
        version=1,
        read_at=read_at,
        fingerprint="a" * 64,
        evidence={"items": []},
        aliases={"names": {"service_1": "checkout", "service_2": "orders-db", "version_1": "v2.1"}},
        summary={"header": None, "card": None},
        model_input=model_input or SAMPLE_MODEL_INPUT,
        wording_state=wording_state,
    )
    db.add(row)
    await db.flush()
    return row


async def _project(db: AsyncSession, tenant: ProvisionedTenant, name: str) -> Project:
    project = Project(
        org_id=tenant.organization.id, slug=f"{name}-{uuid.uuid4().hex[:8]}", name=name
    )
    db.add(project)
    await db.flush()
    return project


async def test_with_no_finding_the_preview_is_a_labelled_sample(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR25."""
    await _save(db, tenant)
    preview = await service.preview(db, org_id=tenant.organization.id)
    assert preview.sample is True and preview.finding_id is None
    assert "built-in sample" in preview.summary[0]
    assert {row.alias for row in preview.aliases} == {"service_1", "service_2", "version_1"}


async def test_the_preview_is_byte_for_byte_what_build_makes_of_the_stored_input(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC36: the body equals `WireFormat.build` over the stored `model_input`, with the key
    shown as a stand-in and never sent here."""
    await _save(db, tenant)
    stored = await _explanation(db, tenant, tenant.project.id, read_at=NOW)

    preview = await service.preview(db, org_id=tenant.organization.id)

    expected = wire_format("anthropic_messages").build(
        explain_request(stored.model_input or {}),
        model="claude-haiku-5-5",
        base_url=CLAUDE,
        key=None,
    )
    assert preview.sample is False and preview.finding_id == stored.finding_id
    assert preview.body.encode() == expected.body
    assert preview.request_sha256 == expected.sha256()
    assert {"name": "x-api-key", "value": "(the saved key)"} in [
        h.model_dump() for h in preview.headers
    ]
    _holds_no_key(preview.model_dump(mode="json"))
    assert {row.alias: row.stands_for for row in preview.aliases}["service_1"] == "checkout"
    assert "facts[].chance" in preview.sendable_fields and "absence" in preview.fact_kinds


async def test_the_preview_skips_opted_out_and_deleted_projects(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC39: a deleted or opted-out project's finding never appears in the preview."""
    await _save(db, tenant)
    kept = await _explanation(db, tenant, tenant.project.id, read_at=NOW - timedelta(hours=2))
    gone = await _project(db, tenant, "gone")
    out = await _project(db, tenant, "staging")
    await _explanation(db, tenant, gone.id, read_at=NOW)
    await _explanation(db, tenant, out.id, read_at=NOW - timedelta(minutes=1))
    gone.deleted_at = NOW
    await service.set_opt_out(
        db,
        actor=_actor(tenant),
        project_id=out.id,
        opted_out=True,
        availability=SELF_HOSTED,
        now=NOW,
    )

    preview = await service.preview(db, org_id=tenant.organization.id)

    assert preview.finding_id == kept.finding_id


# ------------------------------------------------------------------ opt-outs


async def test_opting_out_stops_that_project_s_queued_wording_in_the_same_transaction(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR28, AC39: pending wording in that project becomes `off`; another project's stays."""
    await _save(db, tenant)
    staging = await _project(db, tenant, "staging")
    queued = await _explanation(db, tenant, staging.id, read_at=NOW, wording_state="pending")
    elsewhere = await _explanation(
        db, tenant, tenant.project.id, read_at=NOW, wording_state="pending"
    )

    await service.set_opt_out(
        db,
        actor=_actor(tenant),
        project_id=staging.id,
        opted_out=True,
        availability=SELF_HOSTED,
        now=NOW,
    )

    await db.refresh(queued)
    await db.refresh(elsewhere)
    assert queued.wording_state == "off" and queued.wording_changed_at == NOW
    assert elsewhere.wording_state == "pending"
    listed = {
        row.project_id: row.opted_out
        for row in await service.project_opt_outs(db, org_id=tenant.organization.id)
    }
    assert listed[staging.id] is True and listed[tenant.project.id] is False
    assert "ai.project_opted_out" in [entry.action for entry in await _audit(db, tenant)]
    await service.set_opt_out(
        db,
        actor=_actor(tenant),
        project_id=staging.id,
        opted_out=False,
        availability=SELF_HOSTED,
        now=NOW,
    )
    assert "ai.project_opted_in" in [entry.action for entry in await _audit(db, tenant)]


async def test_another_organization_s_project_cannot_be_opted_out(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    other = await provision_tenant(
        db,
        organization_name="Other",
        email=f"other-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )
    with pytest.raises(NotFoundError):
        await service.set_opt_out(
            db,
            actor=_actor(tenant),
            project_id=other.project.id,
            opted_out=True,
            availability=SELF_HOSTED,
            now=NOW,
        )


# ------------------------------------------------------------------ hosted


async def test_where_ai_is_unavailable_every_write_refuses(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC40, FR31: hosted keeps AI off; so does an install with no `AI_SECRET_KEY`."""
    await _save(db, tenant)
    for unavailable in (
        service.Availability(deployment="hosted", keys=KEYS, insight_replicas=1, app_base_url=""),
        service.Availability(
            deployment="self-hosted", keys=ConfiguredKeys(""), insight_replicas=1, app_base_url=""
        ),
    ):
        view = await service.settings_view(
            db, org_id=tenant.organization.id, may_manage=True, availability=unavailable
        )
        assert view.available is False and view.on is False
        writes = (
            _save(db, tenant, availability=unavailable),
            service.delete_connection(
                db, actor=_actor(tenant), availability=unavailable, email=None
            ),
            service.start_check(db, actor=_actor(tenant), availability=unavailable),
            service.consent(
                db,
                actor=_actor(tenant),
                fingerprint="0" * 64,
                schema_version=SCHEMA_VERSION,
                availability=unavailable,
                now=NOW,
            ),
            service.switch(db, actor=_actor(tenant), on=True, availability=unavailable),
            service.set_opt_out(
                db,
                actor=_actor(tenant),
                project_id=tenant.project.id,
                opted_out=True,
                availability=unavailable,
                now=NOW,
            ),
        )
        for write in writes:
            with pytest.raises(ConflictError):
                await write


# --------------------------------------------------------------- usage and runs


async def _run(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    purpose: str,
    host: str,
    started_at: datetime,
    explanation_id: uuid.UUID | None = None,
    sha: str | None = None,
    model: str = "claude-haiku-5-5",
    fields_sent: list[str] | None = None,
) -> AIRun:
    run = AIRun(
        org_id=tenant.organization.id,
        purpose=purpose,
        wire_format="anthropic_messages",
        model=model,
        provider_host=host,
        prompt_version=PROMPT_VERSION if purpose == "explain" else CHECK_PROMPT_VERSION,
        request_sha256=sha,
        explanation_id=explanation_id,
        input_tokens=1000,
        output_tokens=200,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=3),
        outcome="done",
        fields_sent=fields_sent or [],
    )
    db.add(run)
    await db.flush()
    return run


async def test_usage_states_tokens_the_reset_date_and_labelled_estimates(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """FR29: the cost is an estimate from typed prices, per million tokens."""
    await _save(db, tenant)
    await _run(db, tenant, purpose="check", host=CLAUDE, started_at=NOW - timedelta(hours=1))
    await db.execute(
        text(
            "INSERT INTO ai_usage_monthly (org_id, month, used_tokens) VALUES (:org, :month, 1200)"
        ),
        {"org": tenant.organization.id, "month": NOW.date().replace(day=1)},
    )
    usage = await service.usage(db, org_id=tenant.organization.id, now=NOW)
    assert (usage.used_tokens, usage.monthly_token_limit) == (1200, 2_000_000)
    assert usage.resets_on.isoformat() == "2026-12-01"
    assert usage.estimated_cost == Decimal("0.002")
    assert usage.worst_case_cost == Decimal("10")


async def test_a_run_to_a_host_no_owner_agreed_to_is_marked(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC72: hosts read against the consents recorded in the audit log at that time."""
    await _save(db, tenant)
    await _consent(db, tenant)
    [agreed] = [e for e in await _audit(db, tenant) if e.action == "ai.disclosure_agreed"]
    after = agreed.at + timedelta(minutes=5)
    await _run(
        db,
        tenant,
        purpose="explain",
        host=CLAUDE,
        started_at=after,
        fields_sent=["facts[].chance", "invented"],
    )
    await _run(db, tenant, purpose="explain", host="https://elsewhere.example", started_at=after)
    await _run(db, tenant, purpose="check", host="https://elsewhere.example", started_at=after)

    rows = await service.run_list(db, org_id=tenant.organization.id)

    by_host = {(row.provider_host, row.purpose): row for row in rows}
    assert by_host[(CLAUDE, "explain")].host_consent == "consented"
    assert by_host[(CLAUDE, "explain")].fields_sent == ["facts[].chance"]
    assert by_host[("https://elsewhere.example", "explain")].host_consent == "not_consented"
    assert by_host[("https://elsewhere.example", "check")].host_consent == "not_applicable"


async def test_what_a_run_sent_is_rebuilt_and_its_hash_compared(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC36: matches; after the instructions change, "Cannot be reproduced" with the facts
    and no instructions; an expired version says so; the row holds no content."""
    await _save(db, tenant)
    stored = await _explanation(db, tenant, tenant.project.id, read_at=NOW)
    sent = wire_format("anthropic_messages").build(
        explain_request(stored.model_input or {}),
        model="claude-haiku-5-5",
        base_url=CLAUDE,
        key=KEY,
    )
    check = wire_format("anthropic_messages").build(
        CHECK_REQUEST, model="claude-haiku-5-5", base_url=CLAUDE, key=KEY
    )
    matching = await _run(
        db,
        tenant,
        purpose="explain",
        host=CLAUDE,
        started_at=NOW,
        explanation_id=stored.id,
        sha=sent.sha256(),
    )
    older = await _run(
        db,
        tenant,
        purpose="explain",
        host=CLAUDE,
        started_at=NOW,
        explanation_id=stored.id,
        sha="b" * 64,
    )
    expired = await _run(
        db,
        tenant,
        purpose="explain",
        host=CLAUDE,
        started_at=NOW,
        explanation_id=uuid.uuid4(),
        sha="c" * 64,
    )
    tested = await _run(
        db, tenant, purpose="check", host=CLAUDE, started_at=NOW, sha=check.sha256()
    )

    shown = await service.sent(db, org_id=tenant.organization.id, run_id=matching.id)
    assert shown.match == "matches" and shown.body == sent.body.decode()
    assert shown.statement == "Matches the hash of what was sent."
    changed = await service.sent(db, org_id=tenant.organization.id, run_id=older.id)
    assert changed.match == "cannot_reproduce" and changed.instructions is None
    assert changed.statement == "Cannot be reproduced: nexovue changed since this run."
    assert json.loads(changed.facts or "") == stored.model_input
    assert (
        await service.sent(db, org_id=tenant.organization.id, run_id=expired.id)
    ).match == "expired"
    assert (
        await service.sent(db, org_id=tenant.organization.id, run_id=tested.id)
    ).match == "matches"
    columns = set(AIRun.__table__.columns.keys())
    assert not columns & {"prompt", "response", "body", "text", "content", "wording"}


async def test_owners_and_admins_are_told_when_the_beat_lock_is_exposed(
    db: AsyncSession, tenant: ProvisionedTenant
) -> None:
    """AC43's screen half: shown when the AI account can still take advisory locks, read
    from `has_function_privilege`, and not where `setup`'s revoke held. The grant is made
    inside this test's transaction and rolled back with it."""
    held = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    await db.execute(
        text("GRANT EXECUTE ON FUNCTION pg_try_advisory_lock(bigint) TO nexovue_insight")
    )
    exposed = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=True, availability=SELF_HOSTED
    )
    viewer = await service.settings_view(
        db, org_id=tenant.organization.id, may_manage=False, availability=SELF_HOSTED
    )

    assert held.beat_lock_exposed is False
    assert exposed.beat_lock_exposed is True
    assert viewer.beat_lock_exposed is None
