"""`/releases` against real Postgres and ClickHouse (F28 plan T1.14).

AC26 (another tenant's, another project's and a deleted project's release answer 404 on
every route, to a person and a token), AC27 (paging, the service and version filters, a
version serving since yesterday in today's window, and three different empties), AC28
(a rolling deploy reads side by side with exact counts, while the service's own rollup
shows a smaller mixed change; and what F6 does with it, recorded), AC31 (too little
traffic), AC35 (no minute past the settled minute), AC62 (the most recent block leads),
AC63 (a slow first three minutes left out of p95), and the list reading no ClickHouse.

Postgres rows are written inside one outer transaction per test and rolled back, with
F22's team helpers (`test_findings_cases_live.py`); the version rollup is written as the
worker writes it, under a tenant removed at the end of the run. AC28's F6 half commits a
tenant of its own, as the project job runs on its own connections.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import clickhouse_connect
import httpx
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from clickhouse_connect.driver.client import Client
from environment import TEST_ENVIRONMENT
from nexovue_platform.slo import BUCKET_COUNT
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_findings_cases_live import ASYNC_DSN, Team, _team, db  # noqa: F401  shared fixtures

from nexovue_api.auth.dependencies import (
    Identity,
    Reader,
    get_current_identity,
    get_current_reader,
    get_tenant_repository,
)
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.data.postgres.models import Project, Release, ReleasePeriod, User
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.settings import API_PREFIX, Settings

ADMIN_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
SERVICE: Final = "checkout"
MINUTE: Final = timedelta(minutes=1)
HOUR: Final = timedelta(hours=1)
T0: Final = (datetime.now(UTC) - 3 * HOUR).replace(minute=0, second=0, microsecond=0)
# Bucket 30 is about 160 ms; 40 about 470 ms.
FAST: Final = 30
SLOW: Final = 40


def _parts(dsn: str) -> dict[str, Any]:
    url = urlsplit(dsn)
    return {
        "host": url.hostname or "localhost",
        "port": url.port or 8123,
        "username": url.username or "",
        "password": url.password or "",
        "database": (url.path or "/").lstrip("/") or "default",
    }


@pytest.fixture
def administrator() -> Iterator[Client]:
    client = clickhouse_connect.get_client(**_parts(ADMIN_DSN))
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
async def reader_client() -> AsyncIterator[AsyncClient]:
    client = await clickhouse_connect.get_async_client(**_parts(READER_DSN))
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def acme(db: AsyncSession) -> Team:  # noqa: F811
    team = await _team(db, "Acme")
    delete_after_run("service_versions_1m", str(team.org_id))
    delete_after_run("service_stats_1m", str(team.org_id))
    return team


@pytest.fixture
async def globex(db: AsyncSession) -> Team:  # noqa: F811
    team = await _team(db, "Globex")
    delete_after_run("service_versions_1m", str(team.org_id))
    return team


class _NoClickHouse:
    """AC81: the list and a release read Postgres alone."""

    async def query(self, *_: Any, **__: Any) -> list[Any]:
        raise AssertionError("a Postgres-only route read ClickHouse")


def _client(
    db: AsyncSession,  # noqa: F811
    team: Team,
    person: User | None,
    repository: Any,
    *,
    project_scope: uuid.UUID | None = None,
) -> httpx.AsyncClient:
    """As `person`, or as a token scoped to `project_scope` (the team's project) when None."""
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(settings)
    organization = team.tenant.organization
    reader = (
        Reader(
            org_id=organization.id,
            project_scope=project_scope or team.project_id,
            token_id=uuid.uuid4(),
        )
        if person is None
        else Reader(
            org_id=organization.id, identity=Identity(user=person, organization=organization)
        )
    )

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_current_reader] = lambda: reader
    if person is not None:
        app.dependency_overrides[get_current_identity] = lambda: Identity(
            user=person, organization=organization
        )
    app.dependency_overrides[get_tenant_repository] = lambda: repository
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _get(
    db: AsyncSession,  # noqa: F811
    team: Team,
    path: str,
    *,
    person: User | None = None,
    repository: Any = None,
    as_token: bool = False,
    project_scope: uuid.UUID | None = None,
) -> httpx.Response:
    who = None if as_token else (person or team.member)
    async with _client(
        db, team, who, repository or _NoClickHouse(), project_scope=project_scope
    ) as client:
        return await client.get(f"{API_PREFIX}{path}")


def _repository(client: AsyncClient, team: Team) -> TenantScopedRepository:
    return TenantScopedRepository(client, tenant_id=str(team.org_id))


# ------------------------------------------------------------------- seeding


async def _settle(db: AsyncSession, minute: datetime) -> None:  # noqa: F811
    await db.execute(
        text(
            """
            INSERT INTO detection_sweep (swept_at, settled_minute) VALUES (now(), :minute)
            ON CONFLICT (one_row) DO UPDATE SET settled_minute = EXCLUDED.settled_minute
            """
        ),
        {"minute": minute},
    )


@dataclass(frozen=True)
class Seeded:
    release_id: uuid.UUID
    period_id: uuid.UUID


async def _release(
    db: AsyncSession,  # noqa: F811
    team: Team,
    version: str,
    started_at: datetime,
    *,
    service: str = SERVICE,
    previous: str | None = None,
    ended_at: datetime | None = None,
    already_running: bool = False,
    last_seen_at: datetime | None = None,
    project_id: uuid.UUID | None = None,
) -> Seeded:
    release = Release(
        org_id=team.org_id,
        project_id=project_id or team.project_id,
        service_name=service,
        version=version,
        first_seen_at=started_at,
        last_seen_at=last_seen_at or started_at,
    )
    db.add(release)
    await db.flush()
    period = ReleasePeriod(
        org_id=team.org_id,
        release_id=release.id,
        project_id=release.project_id,
        service_name=service,
        started_at=started_at,
        start_basis="seen",
        ended_at=ended_at,
        previous_version=previous,
        already_running=already_running,
    )
    db.add(period)
    await db.flush()
    return Seeded(release.id, period.id)


def _buckets(requests: int, bucket: int) -> list[int]:
    counts = [0] * BUCKET_COUNT
    counts[bucket] = requests
    return counts


def write_versions(
    client: Client,
    team: Team,
    rows: Iterable[tuple[datetime, str, int, int, int]],
    *,
    service: str = SERVICE,
) -> None:
    """`service_versions_1m` rows (minute, version, requests, errors, bucket), as the
    worker writes them, and the same minutes summed into `service_stats_1m`."""
    by_minute: dict[datetime, list[int]] = {}
    versions, stats = [], []
    for minute, version, requests, errors, bucket in rows:
        versions.append(
            f"('{team.org_id}','{team.project_id}','{service}',{int(minute.timestamp())},"
            f"'{version}',{requests},{errors},{_buckets(requests, bucket)})"
        )
        total = by_minute.setdefault(minute, [0, 0, *([0] * BUCKET_COUNT)])
        total[0] += requests
        total[1] += errors
        total[2 + bucket] += requests
    for minute, total in by_minute.items():
        stats.append(
            f"('{team.org_id}','{team.project_id}','{service}',{int(minute.timestamp())},"
            f"{total[0]},{total[1]},{total[2:]})"
        )
    for start in range(0, len(versions), 400):
        client.command(
            f"""
            INSERT INTO service_versions_1m
                (tenant_id, project_id, service_name, minute, version, request_count,
                 error_count, latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), version, requests,
                   errors, arrayReduce('sumForEachState', [buckets])
            FROM values('tenant_id String, project_id String, service_name String,
                         epoch UInt32, version String, requests UInt64, errors UInt64,
                         buckets Array(UInt32)', {",".join(versions[start : start + 400])})
            """
        )
    for start in range(0, len(stats), 400):
        client.command(
            f"""
            INSERT INTO service_stats_1m
                (tenant_id, project_id, service_name, minute, request_count, error_count,
                 latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), requests, errors,
                   arrayReduce('sumForEachState', [buckets])
            FROM values('tenant_id String, project_id String, service_name String,
                         epoch UInt32, requests UInt64, errors UInt64,
                         buckets Array(UInt32)', {",".join(stats[start : start + 400])})
            """
        )


def minutes(
    version: str,
    first: datetime,
    count: int,
    requests: int,
    errors: int = 0,
    bucket: int = FAST,
) -> list[tuple[datetime, str, int, int, int]]:
    return [(first + MINUTE * offset, version, requests, errors, bucket) for offset in range(count)]


# ---------------------------------------------------------------- AC26


async def test_another_tenant_s_release_is_not_found_by_person_or_token(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    theirs = await _release(db, globex, "v1", T0)
    for path in (f"/releases/{theirs.release_id}", f"/releases/{theirs.release_id}/comparison"):
        assert (await _get(db, acme, path)).status_code == 404, path
        assert (await _get(db, acme, path, as_token=True)).status_code == 404, path


async def test_a_token_for_one_project_cannot_read_another_s_release(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    other = Project(org_id=acme.org_id, slug=f"staging-{uuid.uuid4().hex[:8]}", name="Staging")
    db.add(other)
    await db.flush()
    elsewhere = await _release(db, acme, "v1", T0, project_id=other.id)
    for path in (
        f"/releases/{elsewhere.release_id}",
        f"/releases/{elsewhere.release_id}/comparison",
        f"/releases?project_id={other.id}",
    ):
        assert (await _get(db, acme, path, as_token=True)).status_code == 404, path


async def test_a_deleted_project_s_release_answers_404_on_every_route(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    seeded = await _release(db, acme, "v1", T0)
    await db.execute(
        text("UPDATE projects SET deleted_at = now() WHERE org_id = :org_id AND id = :id"),
        {"org_id": acme.org_id, "id": acme.project_id},
    )
    for path in (
        f"/releases/{seeded.release_id}",
        f"/releases/{seeded.release_id}/comparison",
        f"/releases?project_id={acme.project_id}",
    ):
        assert (await _get(db, acme, path)).status_code == 404, path


# ---------------------------------------------------------------- AC27


async def test_the_list_pages_filters_and_keeps_a_version_serving_since_yesterday(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    now = datetime.now(UTC)
    await _release(db, acme, "v1", now - timedelta(days=1), last_seen_at=now)
    await _release(db, acme, "v2", now - 30 * MINUTE, last_seen_at=now)
    await _release(db, acme, "b1", now - 20 * MINUTE, service="billing", last_seen_at=now)
    base = f"/releases?project_id={acme.project_id}"

    first = (await _get(db, acme, f"{base}&limit=2")).json()
    assert (first["total"], first["services"]) == (3, 2)
    assert [row["version"] for row in first["releases"]] == ["b1", "v2"]
    second = (await _get(db, acme, f"{base}&limit=2&cursor={first['next_cursor']}")).json()
    assert [row["version"] for row in second["releases"]] == ["v1"]
    assert second["next_cursor"] is None
    assert second["releases"][0]["activity"] == "serving"
    assert second["releases"][0]["basis"] == "seen"

    by_service = (await _get(db, acme, f"{base}&service={SERVICE}")).json()
    assert {row["version"] for row in by_service["releases"]} == {"v1", "v2"}
    by_version = (await _get(db, acme, f"{base}&version=v2")).json()
    assert [row["version"] for row in by_version["releases"]] == ["v2"]


async def test_the_three_empties_read_differently(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    base = f"/releases?project_id={acme.project_id}"
    empty = (await _get(db, acme, base)).json()
    assert (empty["has_any"], empty["latest"]) == (False, None)

    old = datetime.now(UTC) - timedelta(days=3)
    await _release(db, acme, "v2.1", old, ended_at=old + HOUR)
    quiet = (await _get(db, acme, base)).json()
    assert quiet["has_any"] and quiet["total"] == 0
    assert (quiet["latest"]["version"], quiet["latest"]["service_name"]) == ("v2.1", SERVICE)

    nothing = (await _get(db, acme, f"{base}&service=nowhere")).json()
    assert nothing["has_any"] and nothing["total"] == 0 and nothing["latest"] is None


async def test_a_release_names_its_periods_and_what_replaced_it(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """`GET /releases/{id}`: Postgres only, no sender text (FR54)."""
    old = await _release(
        db, acme, "v1", T0 - HOUR, already_running=True, ended_at=T0, last_seen_at=T0
    )
    await _release(db, acme, "v2", T0, previous="v1", last_seen_at=T0 + HOUR)

    answer = await _get(db, acme, f"/releases/{old.release_id}", as_token=True)

    assert answer.status_code == 200, answer.text
    shown = answer.json()
    assert (shown["activity"], shown["replaced_by"], shown["basis"]) == ("replaced", "v2", "seen")
    (period,) = shown["periods"]
    assert period["already_running"] and period["ended_at"] is not None
    assert not {"metadata", "url", "source"} & set(shown)


# -------------------------------------------------------------- comparison


async def _comparison(
    db: AsyncSession,  # noqa: F811
    team: Team,
    reader_client: AsyncClient,
    seeded: Seeded,
    query: str = "",
) -> dict[str, Any]:
    answer = await _get(
        db,
        team,
        f"/releases/{seeded.release_id}/comparison{query}",
        repository=_repository(reader_client, team),
    )
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


async def test_a_rolling_deploy_reads_side_by_side_where_the_service_reads_mixed(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC28: 3% on the new version against 0.25% on the old, in the same minutes."""
    write_versions(
        administrator,
        acme,
        minutes("v1", T0 - HOUR, 60, 1000, 2)
        + minutes("v1", T0, 40, 800, 2)
        + minutes("v2", T0, 40, 200, 6),
    )
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 39 * MINUTE)

    found = await _comparison(db, acme, reader_client, seeded)

    (block,) = found["blocks"]
    assert (found["reason"], found["lead"], block["method"]) == (None, "rollout", "side_by_side")
    assert block["minutes_compared"] == 40
    assert (block["this_version"]["requests"], block["this_version"]["errors"]) == (8000, 240)
    assert (block["previous_version"]["requests"], block["previous_version"]["errors"]) == (
        32000,
        80,
    )
    assert block["failure_share"]["reading"] == "higher"
    # The service's own rollup, the same minutes: 0.8% against 0.2% the hour before.
    mixed = administrator.query(
        "SELECT sumIf(error_count, minute >= {t0:DateTime}) / sumIf(request_count, "
        "minute >= {t0:DateTime}) AS during, sumIf(error_count, minute < {t0:DateTime}) / "
        "sumIf(request_count, minute < {t0:DateTime}) AS before FROM service_stats_1m "
        "WHERE tenant_id = {tenant:String} AND project_id = {project:String}",
        parameters={"t0": T0, "tenant": str(acme.org_id), "project": str(acme.project_id)},
    ).first_row
    assert mixed is not None
    during, before = float(mixed[0]), float(mixed[1])
    assert during == pytest.approx(0.008)
    assert before == pytest.approx(0.002)
    new_share = 240 / 8000
    assert during - before < new_share - 80 / 32000


async def test_five_requests_on_one_side_read_too_little_traffic(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC31."""
    write_versions(
        administrator,
        acme,
        minutes("v1", T0 - HOUR, 120, 50) + minutes("v2", T0, 5, 1),
    )
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 59 * MINUTE)

    found = await _comparison(db, acme, reader_client, seeded)

    assert found["reason"] == "too_little_traffic"
    assert found["blocks"] == []
    assert found["traffic"]["this_version"]["requests"] == 5


async def test_a_minute_past_the_settled_minute_is_not_counted(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC35."""
    write_versions(
        administrator, acme, minutes("v1", T0 - HOUR, 60, 100) + minutes("v2", T0, 60, 100)
    )
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 29 * MINUTE)

    found = await _comparison(db, acme, reader_client, seeded)

    (block,) = found["blocks"]
    assert block["this_version"]["requests"] == 30 * 100
    assert found["compared_to"].startswith((T0 + 30 * MINUTE).strftime("%Y-%m-%dT%H:%M"))
    assert found["as_of"] is None


async def test_healthy_during_the_overlap_and_failing_after_leads_with_after(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC62: the opening sentence reads the most recent settled block."""
    write_versions(
        administrator,
        acme,
        minutes("v1", T0 - HOUR, 70, 200)
        + minutes("v2", T0, 10, 100)
        + minutes("v2", T0 + 10 * MINUTE, 50, 200, 6),
    )
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 59 * MINUTE)

    found = await _comparison(db, acme, reader_client, seeded)

    assert found["lead"] == "after_rollout"
    rollout, after = found["blocks"]
    assert rollout["method"] == "side_by_side"
    assert after["method"] == "version_before_after"
    assert after["failure_share"]["reading"] == "higher"


async def test_a_slow_first_three_minutes_read_about_the_same_on_p95(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC63, FR48."""
    write_versions(
        administrator,
        acme,
        minutes("v1", T0 - HOUR, 60, 100)
        + minutes("v2", T0, 3, 100, bucket=SLOW)
        + minutes("v2", T0 + 3 * MINUTE, 57, 100),
    )
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 59 * MINUTE)

    (block,) = (await _comparison(db, acme, reader_client, seeded))["blocks"]

    assert block["method"] == "version_before_after"
    assert block["p95"]["reading"] == "about_same"


# ---------------------------------------------------------- AC28, F6's half


@pytest.mark.parametrize(
    ("errors_per_1000", "opened"),
    [
        # AC28's scenario: 3% failing on a fifth of traffic reads 0.8% on the service.
        (8, "RECORDED"),
        # The control: 30% on the service, so the harness is seen able to open one.
        (300, "critical"),
    ],
)
async def test_what_f6_does_with_a_failing_rollout(
    administrator: Client, errors_per_1000: int, opened: str
) -> None:
    """AC28's recorded fact, not a requirement: four weeks of 0.2% failing, then 40
    minutes at the given share. Asserts what F6 did on 2026-11-08 (`RECORDED_F6_FINDINGS`),
    so a change in F6 shows here and the report is revisited."""
    outcomes, found = await _run_f6(administrator, errors_per_1000)
    assert all(outcome.skipped is None for outcome in outcomes)
    assert not any(outcome.baselines_missing for outcome in outcomes)
    assert sum(outcome.minutes_judged for outcome in outcomes) >= len(outcomes)
    if opened == "RECORDED":
        assert found == RECORDED_F6_FINDINGS
    else:
        assert opened in found


# What F6 opened over AC28's scenario, read on 2026-11-08 (status.md, *Step 1b*): nothing.
RECORDED_F6_FINDINGS: Final[list[str]] = []
F6_SCENARIO_MINUTES: Final = 40


async def _run_f6(administrator: Client, errors_per_1000: int) -> tuple[list[Any], list[str]]:
    """The project job over four weeks of normal and the scenario's minutes, as the
    coordinator runs it; the severities of the findings it opened."""
    from nexovue_scheduler.detection import baselines
    from nexovue_scheduler.detection.project_job import judge_project
    from nexovue_scheduler.detection.store import connect as detection_store

    writer_dsn = os.environ.get(
        "NEXOVUE_CLICKHOUSE_DETECTION_WRITER_DSN",
        "clickhouse://nexovue_detection_writer:nexovue_detection_writer_dev@localhost:18123/nexovue",
    )
    engine = create_async_engine(ASYNC_DSN)
    team = _CommittedTeam(uuid.uuid4(), uuid.uuid4())
    onset = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    delete_after_run("service_stats_1m", str(team.org_id))
    delete_after_run("detection_readings", str(team.org_id))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'F6')"),
                {"id": team.org_id, "slug": f"f6-{team.org_id.hex[:12]}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO projects (id, org_id, slug, name) "
                    "VALUES (:id, :org_id, 'production', 'Production')"
                ),
                {"id": team.project_id, "org_id": team.org_id},
            )
        normal = minutes("", onset - timedelta(days=29), 1, 1000, 2)
        for weeks in range(1, 5):
            normal += minutes("", onset - timedelta(days=7 * weeks) - 200 * MINUTE, 440, 1000, 2)
        normal += minutes("", onset - 200 * MINUTE, 200, 1000, 2)
        _write_service(administrator, team, normal)
        _write_service(
            administrator,
            team,
            minutes("", onset, F6_SCENARIO_MINUTES, 1000, errors_per_1000),
        )
        store = detection_store(READER_DSN, writer_dsn)
        await baselines.compute_project(engine, store, team.project_id, now=onset - 5 * MINUTE)
        outcomes = [
            await judge_project(engine, store, team.project_id, settled=moment, now=moment)
            for moment in (onset + offset * MINUTE for offset in range(F6_SCENARIO_MINUTES))
        ]
        async with engine.connect() as connection:
            severities = [
                row.severity
                for row in await connection.execute(
                    text(
                        "SELECT severity FROM findings "
                        "WHERE org_id = :org_id AND project_id = :project_id"
                    ),
                    {"org_id": team.org_id, "project_id": team.project_id},
                )
            ]
        return outcomes, severities
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": team.org_id}
            )
        await engine.dispose()


@dataclass(frozen=True)
class _CommittedTeam:
    org_id: uuid.UUID
    project_id: uuid.UUID


def _write_service(
    client: Client, team: _CommittedTeam, rows: Iterable[tuple[datetime, str, int, int, int]]
) -> None:
    values = [
        f"('{team.org_id}','{team.project_id}','{SERVICE}',{int(minute.timestamp())},"
        f"{requests},{errors},{_buckets(requests, bucket)})"
        for minute, _, requests, errors, bucket in rows
    ]
    for start in range(0, len(values), 400):
        client.command(
            f"""
            INSERT INTO service_stats_1m
                (tenant_id, project_id, service_name, minute, request_count, error_count,
                 latency_buckets)
            SELECT tenant_id, project_id, service_name, toDateTime(epoch), requests, errors,
                   arrayReduce('sumForEachState', [buckets])
            FROM values('tenant_id String, project_id String, service_name String,
                         epoch UInt32, requests UInt64, errors UInt64,
                         buckets Array(UInt32)', {",".join(values[start : start + 400])})
            """
        )
