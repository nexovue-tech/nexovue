"""`GET /findings/{id}/explanation` and `POST .../read-again`, against real Postgres
(F3 plan T1.10, T1.14; AC23, AC80, AC92, FR47, FR3). Step 2 (T2.4, T2.12): each fact's
link target, `POST .../feedback`, and `/explanations/...` (AC75, AC83, AC99, AC105's
target half).

A token reads the facts in its own project and gets the same 404 as a missing finding
elsewhere; a token or a viewer may not ask for a read; a member may, once a minute. A
stored cause stays held until its bar is met, unless the non-production override is set.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from live import require_stack
from nexovue_platform.evidence import GATHER_EVIDENCE_TASK
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_identity,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.data.postgres.models import FindingExplanation, Job, Project
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.settings import API_PREFIX, Settings
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_findings_explanation_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

CAUSE: Final = {
    "evidence_id": "deploy:d-1",
    "rule": "signed_deploy",
    "strength": "likely",
    "basis": "verified",
    "supported_by": [],
    "same_minute": False,
}
HEADER: Final = [{"text": "Likely: the deploy of "}, {"name": "checkout"}, {"text": " v2.1"}]
CARD: Final = [{"text": "Likely: deploy of "}, {"name": "checkout"}]


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _tenant(db: AsyncSession) -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name="Acme",
        email=f"acme-{uuid.uuid4()}@example.test",
        password="correct horse battery staple",
    )


def _person(tenant: ProvisionedTenant, role: str = "owner") -> Reader:
    tenant.user.role = role
    return Reader(
        org_id=tenant.organization.id,
        identity=Identity(user=tenant.user, organization=tenant.organization),
    )


def _token(tenant: ProvisionedTenant) -> Reader:
    return Reader(
        org_id=tenant.organization.id, project_scope=tenant.project.id, token_id=uuid.uuid4()
    )


def _client(db: AsyncSession, reader: Reader, **settings: Any) -> httpx.AsyncClient:
    from nexovue_api.main import create_app

    configured = Settings(**{**TEST_ENVIRONMENT, **settings})
    app = create_app(configured)

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: configured
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_reader] = lambda: reader
    app.dependency_overrides[get_tenant_repository] = lambda: _NoTelemetry()
    if reader.identity is not None:
        app.dependency_overrides[get_current_identity] = lambda: reader.identity
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _get(db: AsyncSession, reader: Reader, path: str, **settings: Any) -> httpx.Response:
    async with _client(db, reader, **settings) as client:
        return await client.get(f"{API_PREFIX}{path}")


async def _post(db: AsyncSession, reader: Reader, path: str) -> httpx.Response:
    async with _client(db, reader) as client:
        return await client.post(f"{API_PREFIX}{path}")


async def _finding(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    project_id: uuid.UUID | None = None,
    service: str = "checkout",
) -> FindingRow:
    row = FindingRow(
        org_id=tenant.organization.id,
        project_id=project_id or tenant.project.id,
        scope="service",
        service_name=service,
        severity="warning",
        state="open",
        title="checkout is slower than normal",
        signals=[],
        started_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    db.add(row)
    await db.flush()
    return row


async def _version(
    db: AsyncSession,
    finding: FindingRow,
    number: int,
    *,
    cause: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    db.add(
        FindingExplanation(
            org_id=finding.org_id,
            project_id=finding.project_id,
            finding_id=finding.id,
            version=number,
            fingerprint=str(number) * 64,
            evidence={
                "items": [
                    {
                        "id": "deploy:d-1",
                        "kind": "deploy",
                        "basis": "verified",
                        "sentence": [{"text": "Deploy of "}, {"name": "checkout"}, {"text": "."}],
                        "values": {"at": "2026-11-09T10:00:00+00:00"},
                    },
                    {
                        "id": "absence:could_not_read:errors",
                        "kind": "absence",
                        "basis": None,
                        "sentence": [{"text": "Could not read recent errors this time."}],
                        "values": {"reason": "could_not_read", "read": "errors"},
                    },
                ]
            },
            aliases={"names": {"service_1": "checkout"}, "facts": {}},
            likely_cause=cause,
            summary={"header": HEADER, "card": CARD} if cause else {"header": None, "card": None},
            model_input={"schema": 1},
            reads_failed=1,
            **fields,
        )
    )
    await db.flush()


async def test_a_token_reads_the_facts_in_its_project_and_404_elsewhere(db: AsyncSession) -> None:
    """AC23: facts, never words; another project's finding is a 404 like a missing one."""
    acme = await _tenant(db)
    staging = Project(
        org_id=acme.organization.id, slug=f"staging-{uuid.uuid4().hex[:8]}", name="Staging"
    )
    db.add(staging)
    await db.flush()
    mine, elsewhere = await _finding(db, acme), await _finding(db, acme, staging.id)
    await _version(db, mine, 1)

    read = await _get(db, _token(acme), f"/findings/{mine.id}/explanation")
    refused = await _get(db, _token(acme), f"/findings/{elsewhere.id}/explanation")

    assert read.status_code == 200 and refused.status_code == 404
    assert _person(acme).is_person and not _token(acme).is_person
    body = read.json()
    assert body["wording"] is None and body["wording_state"] == "off"
    assert [fact["kind"] for fact in body["facts"]] == ["deploy", "absence"]
    assert body["facts"][1]["read"] == "errors" and body["reads_failed"] == 1


async def test_another_organizations_finding_is_not_found(db: AsyncSession) -> None:
    acme, globex = await _tenant(db), await _tenant(db)
    theirs = await _finding(db, globex)

    assert (await _get(db, _person(acme), f"/findings/{theirs.id}/explanation")).status_code == 404


async def test_a_cause_is_held_until_its_bar_is_met(db: AsyncSession) -> None:
    """AC92: stored, and named on neither the page nor the card, until released; the
    non-production override shows it, with the shared count (FR47)."""
    acme = await _tenant(db)
    first, second = await _finding(db, acme), await _finding(db, acme, service="payments")
    await _version(db, first, 1, cause=CAUSE)
    await _version(db, second, 1, cause=CAUSE)

    held = (await _get(db, _person(acme), f"/findings/{first.id}/explanation")).json()
    shown = (
        await _get(db, _person(acme), f"/findings/{first.id}/explanation", show_held_causes=True)
    ).json()
    feed = (await _get(db, _person(acme), f"/findings?project_id={acme.project.id}")).json()

    assert held["likely_cause"] is None and held["cause_held"] is True
    assert not any(fact["is_cause"] for fact in held["facts"])
    assert {row["likely_cause_sentence"] for row in feed["findings"]} == {None}
    assert _plain(shown["likely_cause"]["header"]) == HEADER and shown["cause_held"] is False
    assert shown["shared_cause_count"] == 2
    assert shown["facts"][0]["is_cause"] is True


async def test_an_earlier_version_reads_by_number_and_a_changed_cause_says_what_it_was(
    db: AsyncSession,
) -> None:
    """AC4, FR3: `?version=` reads an earlier version; the latest names its predecessor's cause."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    await _version(db, finding, 1, cause=CAUSE)
    await _version(
        db, finding, 2, cause={**CAUSE, "evidence_id": "dependency:x", "rule": "dependency_edge"}
    )

    first = (await _get(db, _person(acme), f"/findings/{finding.id}/explanation?version=1")).json()
    latest = (
        await _get(db, _person(acme), f"/findings/{finding.id}/explanation", show_held_causes=True)
    ).json()
    missing = await _get(db, _person(acme), f"/findings/{finding.id}/explanation?version=9")

    assert (first["version"], first["latest_version"]) == (1, 2)
    assert _plain(latest["previous_cause"]) == HEADER
    assert missing.status_code == 404


async def test_before_any_read_the_state_says_reading_or_not_read(db: AsyncSession) -> None:
    acme = await _tenant(db)
    path = f"/findings/{(await _finding(db, acme)).id}/explanation"

    unread = (await _get(db, _person(acme), path)).json()
    assert unread["state"] == "not_read" and unread["facts"] == []

    response = await _post(db, _person(acme, "member"), f"{path}/read-again")
    reading = (await _get(db, _person(acme), path)).json()

    assert response.status_code == 200 and reading["state"] == "reading"


async def test_reading_again_is_once_a_minute_with_the_time_said(db: AsyncSession) -> None:
    """AC80: the second ask inside a minute is 409 with `next_read_at`; one job is queued."""
    acme = await _tenant(db)
    org_id = acme.organization.id
    finding = await _finding(db, acme)
    path = f"/findings/{finding.id}/explanation"
    await _version(db, finding, 1)
    member = _person(acme, "member")

    first = await _post(db, member, f"{path}/read-again")
    second = await _post(db, member, f"{path}/read-again")
    page = (await _get(db, member, path)).json()

    assert first.status_code == 200
    assert second.status_code == 409 and "next_read_at" in second.json()
    assert page["next_read_at"] is not None
    jobs = (
        await db.scalars(
            select(Job.kwargs).where(Job.org_id == org_id, Job.task == GATHER_EVIDENCE_TASK)
        )
    ).all()
    assert [json.loads(json.dumps(kwargs))["reason"] for kwargs in jobs] == ["person"]


async def test_a_viewer_and_a_token_may_not_ask_for_a_read(db: AsyncSession) -> None:
    """AC23's role cases: a viewer is refused, and a token is refused as a token is anywhere."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)

    viewer = await _post(
        db, _person(acme, "viewer"), f"/findings/{finding.id}/explanation/read-again"
    )

    assert viewer.status_code == 403
    queued = await db.scalar(
        text("SELECT count(*) FROM jobs WHERE org_id = :org_id"), {"org_id": acme.organization.id}
    )
    assert queued == 0


async def test_the_finding_says_until_when_its_raw_data_is_kept(db: AsyncSession) -> None:
    """Design, *Links*: `raw_kept_until` from the project's raw retention."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)

    body = (await _get(db, _person(acme), f"/findings/{finding.id}")).json()

    kept = datetime.fromisoformat(body["raw_kept_until"])
    assert kept > finding.started_at


def _plain(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in segment.items() if value is not None} for segment in segments
    ]


class _NoTelemetry:
    """A repository with nothing in it: the page's readings are empty."""

    def __getattr__(self, name: str) -> Any:
        async def nothing(*_: object, **__: object) -> list[Any]:
            return []

        return nothing


# ------------------------------------------------------------------ Step 2


async def _send(
    db: AsyncSession,
    reader: Reader,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    **settings: Any,
) -> httpx.Response:
    async with _client(db, reader, **settings) as client:
        return await client.request(method, f"{API_PREFIX}{path}", json=body)


async def _audit(db: AsyncSession, tenant: ProvisionedTenant, action: str) -> list[Any]:
    return list(
        await db.execute(
            text("SELECT target, detail FROM audit_log WHERE org_id = :org_id AND action = :a"),
            {"org_id": tenant.organization.id, "a": action},
        )
    )


def _rate(version: int, helpful: bool, reason: str | None = None) -> dict[str, Any]:
    return {"version": version, "helpful": helpful, "reason": reason}


async def test_a_member_rates_once_per_version_and_it_is_audited(db: AsyncSession) -> None:
    """FR20, AC75: "Yes", or "No" with a listed reason; a second rating replaces the first."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    await _version(db, finding, 1, cause=CAUSE)
    member = _person(acme, "member")
    path = f"/findings/{finding.id}/explanation/feedback"

    first = await _send(db, member, "POST", path, _rate(1, True), SHOW_HELD_CAUSES=True)
    second = await _send(
        db, member, "POST", path, _rate(1, False, "wrong_cause"), SHOW_HELD_CAUSES=True
    )
    read = await _get(db, member, f"/findings/{finding.id}/explanation", SHOW_HELD_CAUSES=True)

    assert first.status_code == 200 and second.status_code == 200
    assert read.json()["my_rating"] == {"version": 1, "helpful": False, "reason": "wrong_cause"}
    assert read.json()["rating_reasons"] == ["wrong_cause", "not_useful"]
    count = await db.scalar(
        text("SELECT count(*) FROM finding_explanation_ratings WHERE org_id = :org_id"),
        {"org_id": acme.organization.id},
    )
    assert count == 1
    audited = await _audit(db, acme, "finding.explanation_rated")
    assert [row.detail["reason"] for row in audited] == [None, "wrong_cause"]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (_rate(1, False), 422),
        (_rate(1, True, "not_useful"), 422),
        (_rate(1, False, "free text"), 422),
        (_rate(1, False, "wrong_cause"), 422),
        (_rate(1, False, "bad_wording"), 422),
        (_rate(9, True), 404),
    ],
    ids=[
        "no without reason",
        "yes with reason",
        "unknown reason",
        "no cause stated",
        "no wording shown",
        "no such version",
    ],
)
async def test_a_rating_must_fit_what_was_shown(
    db: AsyncSession, body: dict[str, Any], status: int
) -> None:
    """AC75: no "wrong cause" with no stated cause (a held one is not stated), no "poor
    wording" with no wording, no free text."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    await _version(db, finding, 1, cause=CAUSE)

    refused = await _send(
        db, _person(acme, "member"), "POST", f"/findings/{finding.id}/explanation/feedback", body
    )

    assert refused.status_code == status


async def test_a_viewer_and_a_token_may_not_rate(db: AsyncSession) -> None:
    """AC23, AC75: the rating is not a viewer's, and a token is refused as anywhere."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    await _version(db, finding, 1)
    path = f"/findings/{finding.id}/explanation/feedback"

    viewer = await _send(db, _person(acme, "viewer"), "POST", path, _rate(1, True))
    token_read = await _get(db, _token(acme), f"/findings/{finding.id}/explanation")

    # A token never reaches the route: its gate needs a signed-in person, which
    # `test_authorization_coverage.py` holds for every mutating route.
    assert viewer.status_code == 403
    assert token_read.json()["rating_reasons"] == [] and token_read.json()["my_rating"] is None


async def test_a_rating_carries_to_a_later_version_until_the_cause_changes(
    db: AsyncSession,
) -> None:
    """AC83: version 2 keeps version 1's cause and its rating; version 3 names another."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    other = {**CAUSE, "evidence_id": "dependency:abc", "rule": "dependency_finding"}
    await _version(db, finding, 1, cause=CAUSE)
    await _version(db, finding, 2, cause=CAUSE)
    await _version(db, finding, 3, cause=other)
    member = _person(acme, "member")
    await _send(
        db,
        member,
        "POST",
        f"/findings/{finding.id}/explanation/feedback",
        _rate(1, True),
        SHOW_HELD_CAUSES=True,
    )

    async def mine(version: int) -> Any:
        read = await _get(
            db,
            member,
            f"/findings/{finding.id}/explanation?version={version}",
            SHOW_HELD_CAUSES=True,
        )
        return read.json()["my_rating"]

    assert await mine(2) == {"version": 1, "helpful": True, "reason": None}
    assert await mine(3) is None


async def test_a_fact_carries_what_its_link_needs_and_nothing_else(db: AsyncSession) -> None:
    """AC22, AC105's API half: an error group's id and services; a dependency's caller,
    callee, the attribute that named it and what moved."""
    acme = await _tenant(db)
    finding = await _finding(db, acme)
    db.add(
        FindingExplanation(
            org_id=finding.org_id,
            project_id=finding.project_id,
            finding_id=finding.id,
            version=1,
            fingerprint="f" * 64,
            evidence={
                "items": [
                    {
                        "id": "error_group:g-1",
                        "kind": "error_group",
                        "basis": "telemetry_text",
                        "sentence": [{"text": "New error"}],
                        "values": {
                            "group_id": "g-1",
                            "services": ["checkout"],
                            "first_seen": "2026-11-09T10:00:00+00:00",
                            "exception_type": "TimeoutError",
                        },
                    },
                    {
                        "id": "dependency:x",
                        "kind": "dependency",
                        "basis": "telemetry",
                        "sentence": [{"text": "Calls"}],
                        "values": {
                            "caller": "checkout",
                            "callee": "(payments)",
                            "callee_attr": "peer.service",
                            "failure_share_before": 0.004,
                            "failure_share_during": 0.06,
                            "p95_before": 120.0,
                            "p95_during": 480.0,
                            "calls_during": 900,
                        },
                    },
                    {
                        "id": "traffic:t",
                        "kind": "traffic",
                        "basis": "telemetry",
                        "sentence": [{"text": "Traffic"}],
                        "values": {"service": "checkout"},
                    },
                ]
            },
            aliases={},
            summary={"header": None, "card": None},
        )
    )
    await db.flush()

    facts = (await _get(db, _person(acme), f"/findings/{finding.id}/explanation")).json()["facts"]

    error, dependency, traffic = (fact["target"] for fact in facts)
    assert (error["kind"], error["group_id"], error["services"]) == (
        "error_group",
        "g-1",
        ["checkout"],
    )
    assert (dependency["caller"], dependency["callee"], dependency["callee_attr"]) == (
        "checkout",
        "(payments)",
        "peer.service",
    )
    assert dependency["failure_share_during"] == 0.06 and dependency["p95_before"] == 120.0
    assert "TimeoutError" not in json.dumps(error) and "calls_during" not in dependency
    assert traffic is None


PLANTED_NAME: Final = "planted-service-name-7f3"


async def test_rule_counts_are_counts_only_and_need_detection_settings(db: AsyncSession) -> None:
    """AC99: an admin reads counts per rule, and a planted name never appears; a viewer and
    a member are refused."""
    acme = await _tenant(db)
    finding = await _finding(db, acme, service=PLANTED_NAME)
    await _version(db, finding, 1, cause=CAUSE)
    await _send(
        db,
        _person(acme, "member"),
        "POST",
        f"/findings/{finding.id}/explanation/feedback",
        _rate(1, False, "wrong_cause"),
        SHOW_HELD_CAUSES=True,
    )

    member = await _send(db, _person(acme, "member"), "GET", "/explanations/rule-counts")
    owner = _person(acme)
    counts = await _send(db, owner, "GET", "/explanations/rule-counts", SHOW_HELD_CAUSES=True)
    held = await _send(db, owner, "GET", "/explanations/rule-counts")

    signed = next(row for row in counts.json()["rules"] if row["rule"] == "signed_deploy")
    assert (signed["named"], signed["wrong_cause"], signed["stopped"]) == (1, 1, False)
    assert PLANTED_NAME not in counts.text and "checkout" not in counts.text
    held_signed = next(row for row in held.json()["rules"] if row["rule"] == "signed_deploy")
    assert held_signed["named"] == 0 and held.json()["without_cause_not_useful"] == 1
    assert member.status_code == 403


async def test_stopping_a_rule_is_audited_and_stays_in_its_organization(
    db: AsyncSession,
) -> None:
    """AC99: stopped here, written down, shown as stopped; another organization's rule
    still runs; an unknown rule is refused; a viewer may not stop one."""
    acme, globex = await _tenant(db), await _tenant(db)

    stopped = await _send(db, _person(acme), "PUT", "/explanations/stopped-rules/signed_deploy")
    again = await _send(db, _person(acme), "PUT", "/explanations/stopped-rules/signed_deploy")
    unknown = await _send(db, _person(acme), "PUT", "/explanations/stopped-rules/new_error")
    viewer = await _send(
        db, _person(acme, "viewer"), "PUT", "/explanations/stopped-rules/traffic_rose"
    )
    mine = await _send(db, _person(acme), "GET", "/explanations/rule-counts")
    theirs = await _send(db, _person(globex), "GET", "/explanations/rule-counts")

    assert stopped.json() == {"rule": "signed_deploy", "stopped": True}
    assert again.status_code == 200 and unknown.status_code == 422
    assert viewer.status_code == 403

    def stopped_rules(response: httpx.Response) -> list[str]:
        return [row["rule"] for row in response.json()["rules"] if row["stopped"]]

    assert stopped_rules(mine) == ["signed_deploy"] and stopped_rules(theirs) == []
    assert len(await _audit(db, acme, "explanation.rule_stopped")) == 1

    resumed = await _send(db, _person(acme), "DELETE", "/explanations/stopped-rules/signed_deploy")
    assert resumed.json() == {"rule": "signed_deploy", "stopped": False}
    assert len(await _audit(db, acme, "explanation.rule_resumed")) == 1
