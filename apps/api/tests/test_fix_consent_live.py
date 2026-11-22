"""Source consent, code locations, readiness and the GitHub link's routes, against real
Postgres (F5 plan T3.4, T3.5, T3.6, T3.17; spec AC9, AC10a's consent half, AC11, AC12a,
AC12c, AC44, A18, and FR7's exact strings).

Each test builds its own tenant inside a transaction rolled back afterwards.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import general_fix_rows as rows
import pytest
import source_fix_rows as source
from general_fix_rows import POSTGRES_DSN, reachable
from live import require_stack
from nexovue_platform.checks.sealing import ConfiguredKeys, generate_key
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.auth.sessions import Session
from nexovue_api.connections import user_link
from nexovue_api.data.postgres.models import (
    AIConnection,
    FindingFix,
    FixSourceConsent,
    GitHubLinkAttempt,
)
from nexovue_api.findings.fix_consent import CONSENT_SENTENCES
from nexovue_api.settings import API_PREFIX

db = rows.db
now = rows.now

require_stack(
    reachable=reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_fix_consent_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

CONSENTS = API_PREFIX + "/connections/github/repositories"
ANSWER: dict[str, Any] = {"require_github_access": False}


async def _agree(
    db: AsyncSession, identity: Any, repository: Any, query: str, body: Any = None
) -> Any:
    async with source.client(db, identity) as http:
        return await http.put(
            f"{CONSENTS}/{repository.id}/source-consents?{query}", json=body or ANSWER
        )


async def _source_offer(db: AsyncSession, tenant: Any, finding: Any) -> Any:
    async with source.client(db, rows.person(tenant)) as http:
        return (await http.get(source.path(finding, "/fixes/source"))).json()


@pytest.mark.parametrize("role", ["admin", "member"])
async def test_ac10a_only_an_owner_may_agree(db: AsyncSession, now: datetime, role: str) -> None:
    tenant, _, repository = await source.ready(db, now)

    response = await _agree(db, rows.person(tenant, role), repository, "folder=services%2Fb")

    assert response.status_code == 403


@pytest.mark.parametrize("query", ["", "folder=a&scope=whole-repository", "scope=everything"])
async def test_ac10a_the_route_takes_exactly_one_scope(
    db: AsyncSession, now: datetime, query: str
) -> None:
    tenant, _, repository = await source.ready(db, now)

    response = await _agree(db, rows.person(tenant, "owner"), repository, query)

    assert response.status_code == 422


async def test_ac10a_consent_does_not_save_without_the_access_answer(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, repository = await source.ready(db, now)

    response = await _agree(db, rows.person(tenant, "owner"), repository, "folder=x", body={"x": 1})

    assert response.status_code == 422


async def test_ac10a_a_folder_with_a_slash_and_the_whole_repository_are_each_taken(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, repository = await source.ready(db, now)
    owner = rows.person(tenant, "owner")

    folder = await _agree(db, owner, repository, "folder=services%2Fbilling")
    whole = await _agree(db, owner, repository, "scope=whole-repository")

    assert folder.status_code == 200 and folder.json()["folder_scope"] == "services/billing"
    assert whole.status_code == 200 and whole.json()["folder_scope"] is None
    audit = await db.scalar(
        text(
            "SELECT count(*) FROM audit_log WHERE org_id = :org "
            "AND action = 'ai.source_disclosure_agreed'"
        ),
        {"org": tenant.organization.id},
    )
    assert audit == 2


async def test_ac12c_two_services_in_one_repository_consent_by_folder(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    other = await rows.finding(
        db, tenant, now, service_name="billing", started_at=now - timedelta(hours=1)
    )
    await source.location(db, tenant, repository, folder="services/billing", service="billing")

    before = await _source_offer(db, tenant, other)
    await _agree(db, rows.person(tenant, "owner"), repository, "folder=services%2Fbilling")
    after = await _source_offer(db, tenant, other)
    first = await _source_offer(db, tenant, finding)

    assert before["offer"] is None
    assert after["offer"] is not None and first["offer"] is not None


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("the address", {"base_url": "https://elsewhere.example"}),
        ("the model", {"model": "another-model"}),
        ("a new key", {"key_id": uuid.uuid4()}),
        ("the format", {"wire_format": "anthropic_messages"}),
    ],
)
async def test_ac9_a_provider_change_stops_the_next_request_and_touches_no_consent_row(
    db: AsyncSession, now: datetime, label: str, change: dict[str, Any]
) -> None:
    tenant, finding, _ = await source.ready(db, now)
    # This organization's rows only: other suites commit their own while this one runs.
    own_consents = select(FixSourceConsent.__table__).where(
        FixSourceConsent.org_id == tenant.organization.id
    )
    consent_before = (await db.execute(own_consents)).all()
    ai = await db.scalar(select(AIConnection).where(AIConnection.org_id == tenant.organization.id))
    assert ai is not None
    for name, value in change.items():
        setattr(ai, name, value)
    if "base_url" in change:
        ai.consented_base_url = change["base_url"]
    await db.flush()

    async with source.client(db, rows.person(tenant)) as http:
        response = await http.post(source.path(finding, "/fixes"), json={})

    # F3 ends its own consent first on a new address, key or format (FR9 row 2, `ai_off`); the model
    # alone is F5's (row 10). Either way the request stops, and no consent row
    # was touched to stop it (status.md, *Open decisions*).
    expected = "no_consent" if label == "the model" else "ai_off"
    assert response.status_code == 409 and response.json()["code"] == expected, label
    assert (await db.execute(own_consents)).all() == consent_before


async def test_withdrawing_ends_work_in_flight_and_deletes_its_stored_code(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    async with source.client(db, rows.person(tenant)) as http:
        await http.post(source.path(finding, "/fixes"), json={})

    async with source.client(db, rows.person(tenant, "owner")) as http:
        response = await http.delete(
            f"{CONSENTS}/{repository.id}/source-consents?folder=services%2Fshop"
        )

    assert response.status_code == 200 and response.json()["code_deleted"] == 1
    row = await db.scalar(select(FindingFix).where(FindingFix.finding_id == finding.id))
    assert row is not None
    await db.refresh(row)
    assert (row.state, row.reason) == ("failed", "consent_ended_in_flight")
    assert row.path_candidates is None and row.code_deleted_reason == "consent_withdrawn"


async def test_fr7_the_overview_carries_the_exact_sentences_and_who_can_ask(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, _ = await source.ready(db, now)

    async with source.client(db, rows.person(tenant)) as http:
        body = (await http.get(f"{CONSENTS}/source-consent")).json()

    assert body["sentences"] == list(CONSENT_SENTENCES)
    assert body["sentences"][0].startswith("Nexovue cannot see or control what this provider")
    assert body["requesters"] == 1 and body["provider_host"] == "provider.example"
    assert body["repositories"][0]["consents"][0]["standing"] == "live"


async def test_ac11_a_support_view_reads_no_owner_paths(db: AsyncSession, now: datetime) -> None:
    tenant, _, _repository = await source.ready(db, now)
    consent = await db.scalar(
        select(FixSourceConsent).where(FixSourceConsent.org_id == tenant.organization.id)
    )
    assert consent is not None
    consent.extra_excluded_paths = ["src/private.py"]
    await db.flush()

    async with source.client(db, rows.support_view(tenant, now)) as http:
        body = (await http.get(f"{CONSENTS}/source-consent")).json()

    assert body["repositories"][0]["consents"][0]["extra_excluded_paths"] is None


# ------------------------------------------------------------- code locations


async def test_ac12a_a_bulk_form_saves_every_row_and_audits_each(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, repository = await source.ready(db, now)
    body = {
        "rows": [
            {
                "service": "billing",
                "repository_id": str(repository.id),
                "folder": "services/billing",
            },
            {"service": "search", "repository_id": str(repository.id), "folder": ""},
        ]
    }

    async with source.client(db, rows.person(tenant, "admin")) as http:
        response = await http.put(f"{API_PREFIX}/code-locations", json=body)

    assert response.status_code == 200, response.text
    saved = {each["service"]: each for each in response.json()["locations"]}
    assert saved["search"]["folder"] is None and saved["billing"]["consented"] is False
    assert saved["checkout"]["consented"] is True
    audits = await db.scalar(
        text(
            "SELECT count(*) FROM audit_log WHERE org_id = :org "
            "AND action = 'fix.code_location_changed'"
        ),
        {"org": tenant.organization.id},
    )
    assert audits == 2


async def test_ac12a_a_bulk_form_names_the_row_it_refused_and_saves_none(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, repository = await source.ready(db, now)
    body = {
        "rows": [
            {"service": "billing", "repository_id": str(repository.id), "folder": "ok"},
            {"service": "search", "repository_id": str(repository.id), "folder": "a/../b"},
        ]
    }

    async with source.client(db, rows.person(tenant, "admin")) as http:
        response = await http.put(f"{API_PREFIX}/code-locations", json=body)

    assert response.status_code == 422 and response.json()["detail"].startswith("Row 2:")
    found = await db.scalar(
        text("SELECT count(*) FROM service_code_locations WHERE org_id = :org"),
        {"org": tenant.organization.id},
    )
    assert found == 1


async def test_ac12a_a_project_s_override_wins_over_the_organization_s(
    db: AsyncSession, now: datetime
) -> None:
    tenant, finding, repository = await source.ready(db, now)
    await source.location(db, tenant, repository, folder="elsewhere", project_id=tenant.project.id)

    body = await _source_offer(db, tenant, finding)

    assert body["offer"] is None  # the override's folder has no consent


async def test_a_member_cannot_set_a_code_location(db: AsyncSession, now: datetime) -> None:
    tenant, _, repository = await source.ready(db, now)

    async with source.client(db, rows.person(tenant, "member")) as http:
        response = await http.put(
            f"{API_PREFIX}/code-locations",
            json={"rows": [{"service": "x", "repository_id": str(repository.id)}]},
        )

    assert response.status_code == 403


# ------------------------------------------------------------------- readiness


async def test_ac44_readiness_counts_each_result_and_says_what_it_counts(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, _ = await source.ready(db, now)
    await rows.finding(db, tenant, now, service_name="search")
    await rows.finding(db, tenant, now, scope="project", service_name=None)

    async with source.client(db, rows.person(tenant)) as http:
        body = (await http.get(f"{API_PREFIX}/fix-readiness?project_id={tenant.project.id}")).json()

    assert (body["findings"], body["ready"], body["ready_without_stack"]) == (3, 1, 1)
    assert body["project_finding"] == 1 and body["services_without_location"] == ["search"]
    assert body["sentence"].startswith("Counts only the evidence Nexovue holds")


async def test_ac44_with_no_findings_it_says_there_is_nothing_to_estimate(
    db: AsyncSession, now: datetime
) -> None:
    tenant = await rows.tenant(db)

    async with source.client(db, rows.person(tenant)) as http:
        body = (await http.get(f"{API_PREFIX}/fix-readiness?project_id={tenant.project.id}")).json()

    assert (
        body["sentence"] == "No findings in the last 30 days, so there is nothing to estimate yet."
    )


# ---------------------------------------------------------------- the GitHub link


def _session(tenant: Any, created_at: datetime) -> Session:
    return Session(
        user_id=str(tenant.user.id), org_id=str(tenant.organization.id), created_at=created_at
    )


async def _with_client_id(db: AsyncSession, tenant: Any) -> None:
    await db.execute(
        text("UPDATE connections SET github_client_id = 'Iv1.x' WHERE org_id = :org"),
        {"org": tenant.organization.id},
    )


async def test_a18_another_session_cannot_complete_a_person_s_link(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, _ = await source.ready(db, now)
    await _with_client_id(db, tenant)
    mine = _session(tenant, now - timedelta(hours=1))
    started = await user_link.start(
        db,
        org_id=tenant.organization.id,
        user_id=tenant.user.id,
        session=mine,
        app_base_url="https://nexovue.example",
        now=now,
    )
    state = started.authorize_url.split("state=")[1].split("&")[0]

    with pytest.raises(Exception, match="not valid"):
        await user_link.complete(
            db,
            org_id=tenant.organization.id,
            user_id=tenant.user.id,
            session=_session(tenant, now - timedelta(minutes=5)),
            state=state,
            code="abc123",
            keys=ConfiguredKeys(generate_key()),
            now=now,
        )


async def test_ac15_a_completed_link_seals_the_code_and_its_job_holds_ids_only(
    db: AsyncSession, now: datetime
) -> None:
    tenant, _, _ = await source.ready(db, now)
    await _with_client_id(db, tenant)
    session = _session(tenant, now - timedelta(hours=1))
    started = await user_link.start(
        db,
        org_id=tenant.organization.id,
        user_id=tenant.user.id,
        session=session,
        app_base_url="https://nexovue.example",
        now=now,
    )
    state = started.authorize_url.split("state=")[1].split("&")[0]

    view = await user_link.complete(
        db,
        org_id=tenant.organization.id,
        user_id=tenant.user.id,
        session=session,
        state=state,
        code="code-shown-once",
        keys=ConfiguredKeys(generate_key()),
        now=now,
    )

    attempt = await db.scalar(select(GitHubLinkAttempt).where(GitHubLinkAttempt.id == view.id))
    assert attempt is not None and attempt.status == "running"
    assert attempt.sealed_code and b"code-shown-once" not in bytes(attempt.sealed_code)
    jobs = list(
        await db.scalars(
            text("SELECT kwargs::text FROM jobs WHERE org_id = :org"),
            {"org": tenant.organization.id},
        )
    )
    assert jobs and all("code-shown-once" not in each for each in jobs)
    with pytest.raises(Exception, match="not valid"):
        await user_link.complete(
            db,
            org_id=tenant.organization.id,
            user_id=tenant.user.id,
            session=session,
            state=state,
            code="again",
            keys=ConfiguredKeys(generate_key()),
            now=now,
        )
