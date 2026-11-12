"""`/releases` against real Postgres and ClickHouse (F28 plan T1.14, Step 4).

AC26 (another tenant's, another project's and a deleted project's release answer 404 on
every route, to a person and a token), AC27 (paging, the service and version filters, a
version serving since yesterday in today's window, and three different empties), AC28
(a rolling deploy reads side by side with exact counts, while the service's own rollup
shows a smaller mixed change; and what F6 does with it, recorded), AC31 (too little
traffic), AC35 (no minute past the settled minute), AC62 (the most recent block leads),
AC63 (a slow first three minutes left out of p95), and the list reading no ClickHouse.

Step 4 (T4.1, T4.3, T4.4): AC23, AC29 with last week, AC30, AC32's every reason, AC36,
AC41, AC64, AC65's five versions, AC67, AC69, AC76 and AC78.

Postgres rows are written inside one outer transaction per test and rolled back, with
F22's team helpers (`test_findings_cases_live.py`); the version rollup is written as the
worker writes it, under a tenant removed at the end of the run. AC28's F6 half commits a
tenant of its own, as the project job runs on its own connections.
"""

from __future__ import annotations

import json
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
from nexovue_api.data.postgres.models import (
    DeployEvent,
    Finding,
    Project,
    Release,
    ReleasePeriod,
    ServiceLastSeen,
    User,
)
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
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
    for table in ("service_versions_1m", "service_stats_1m", "spans", "logs"):
        delete_after_run(table, str(team.org_id))
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


async def _store_reading(
    db: AsyncSession,  # noqa: F811
    period_id: uuid.UUID,
    reading: dict[str, Any],
) -> None:
    await db.execute(
        text("UPDATE release_periods SET last_reading = CAST(:r AS jsonb) WHERE id = :id"),
        {"r": json.dumps(reading), "id": period_id},
    )


_SIDES: Final = {
    "this": {"requests": 6012, "errors": 180, "p95_ms": 412.0},
    "previous": {"requests": 5844, "errors": 12, "p95_ms": 290.0},
}


async def test_the_list_flags_a_stored_higher_reading_with_its_counts_and_nothing_else(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC81, FR55: "failure share higher" or "p95 higher" with counts, from the reading a
    job stored; no flag for another reading, the time method, or none; the versions
    combined in a compared minute said; and no ClickHouse read (`_NoClickHouse`)."""
    both = await _release(db, acme, "v2", T0, previous="v1", service="a")
    await _store_reading(
        db,
        both.period_id,
        {
            "method": "side_by_side",
            "failure_share": "higher",
            "p95": "higher",
            "versions_combined": True,
            **_SIDES,
        },
    )
    same = await _release(db, acme, "v3", T0, previous="v2", service="b")
    await _store_reading(
        db,
        same.period_id,
        {"method": "version_before_after", "failure_share": "about_same", "p95": "lower", **_SIDES},
    )
    by_time = await _release(db, acme, "v4", T0, service="c")
    await _store_reading(
        db, by_time.period_id, {"method": "time_before_after", "failure_share": "higher", **_SIDES}
    )
    await _release(db, acme, "v5", T0, service="d")
    window = f"from={(T0 - HOUR).isoformat()}&to={(T0 + HOUR).isoformat()}".replace("+", "%2B")

    listed = (await _get(db, acme, f"/releases?project_id={acme.project_id}&{window}")).json()

    rows = {row["service_name"]: row for row in listed["releases"]}
    assert rows["a"]["flags"] == [
        {
            "measure": "failure_share",
            "this_requests": 6012,
            "this_errors": 180,
            "this_p95_ms": 412.0,
            "previous_requests": 5844,
            "previous_errors": 12,
            "previous_p95_ms": 290.0,
        },
        {
            "measure": "p95",
            "this_requests": 6012,
            "this_errors": 180,
            "this_p95_ms": 412.0,
            "previous_requests": 5844,
            "previous_errors": 12,
            "previous_p95_ms": 290.0,
        },
    ]
    assert [rows[name]["flags"] for name in ("b", "c", "d")] == [[], [], []]
    assert [rows[name]["versions_combined"] for name in ("a", "b", "c", "d")] == [
        True,
        False,
        False,
        False,
    ]


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


async def test_a_service_with_no_version_compares_by_time_from_its_period_s_start(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """Design 4, step 6, with the founder's answer of 2026-11-09: a deploy reported failed
    an hour before one reported finished is compared around the finished one, where its
    period starts, not from the failed report."""
    failed_at, finished_at = T0 - HOUR, T0
    write_versions(administrator, acme, minutes("", T0 - 3 * HOUR, 3 * 60 + 40, 100))
    release = Release(
        org_id=acme.org_id,
        project_id=acme.project_id,
        service_name=SERVICE,
        version="v2",
        first_reported_at=failed_at,
        last_reported_at=finished_at,
        report_count=2,
        last_state="finished",
    )
    db.add(release)
    await db.flush()
    period = ReleasePeriod(
        org_id=acme.org_id,
        release_id=release.id,
        project_id=acme.project_id,
        service_name=SERVICE,
        started_at=finished_at,
        start_basis="reported",
        previous_version="v1",
    )
    db.add(period)
    await db.flush()
    await _settle(db, T0 + 39 * MINUTE)

    found = await _comparison(db, acme, reader_client, Seeded(release.id, period.id))

    (block,) = found["blocks"]
    assert block["method"] == "time_before_after"
    assert block["from"].startswith(finished_at.strftime("%Y-%m-%dT%H:%M"))
    assert block["minutes_compared"] == 40


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


# ------------------------------------------------------------- markers, AC38


async def _deploys(
    db: AsyncSession,  # noqa: F811
    team: Team,
    at: Iterable[datetime],
    *,
    service: str = SERVICE,
    version: str = "v1",
    state: str = "finished",
) -> None:
    db.add_all(
        DeployEvent(
            org_id=team.org_id,
            project_id=team.project_id,
            version=version,
            service_name=service,
            deployed_at=moment,
            source="ci",
            delivery_id=f"{service}-{version}-{state}-{moment.isoformat()}",
            event_metadata={},
            state=state,
        )
        for moment in at
    )
    await db.flush()


async def test_a_window_last_week_shows_that_week_s_marks(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC38: 60 deploys over 60 days; a window a week ago reads its own, not the latest."""
    now = datetime.now(UTC).replace(microsecond=0)
    await _deploys(db, acme, [now - timedelta(days=day) for day in range(60)])
    week_ago = now - timedelta(days=7)

    answer = await _get(
        db,
        acme,
        f"/releases/markers?project_id={acme.project_id}"
        f"&from={_iso(week_ago - HOUR)}&to={_iso(week_ago + HOUR)}",
    )

    assert answer.status_code == 200, answer.text
    found = answer.json()
    assert [mark["at"][:19] for mark in found["markers"]] == [week_ago.isoformat()[:19]]
    assert found["truncated"] is False


async def test_marks_carry_state_basis_and_no_sender_text(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC38, AC45, FR37: a deploy of each state, a counted start, a reported period's
    start drawn once (as its deploy), an already-running period not drawn; filtered by
    service; exactly the allowed fields."""
    await _deploys(db, acme, [T0], version="v2", state="failed")
    await _deploys(db, acme, [T0 + MINUTE], version="v2", state="rolled_back")
    await _deploys(db, acme, [T0 + MINUTE], service="payments", version="p1")
    await _release(db, acme, "v3", T0 + 2 * MINUTE, previous="v2")
    await _release(db, acme, "v0", T0 - MINUTE, already_running=True)

    answer = await _get(
        db,
        acme,
        f"/releases/markers?project_id={acme.project_id}&service={SERVICE}"
        f"&from={_iso(T0 - HOUR)}&to={_iso(T0 + HOUR)}",
    )

    marks = answer.json()["markers"]
    assert [(m["version"], m["basis"], m["state"]) for m in marks] == [
        ("v3", "seen", None),
        ("v2", "reported", "rolled_back"),
        ("v2", "reported", "failed"),
    ]
    assert {key for mark in marks for key in mark} == {
        "release_id",
        "period_id",
        "deploy_id",
        "service_name",
        "version",
        "at",
        "basis",
        "state",
    }


async def test_past_500_marks_the_newest_are_kept_and_the_answer_says_so(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC38, FR35: the cap sentence's fact."""
    await _deploys(db, acme, [T0 + timedelta(seconds=n) for n in range(501)])

    answer = await _get(
        db,
        acme,
        f"/releases/markers?project_id={acme.project_id}&from={_iso(T0)}&to={_iso(T0 + HOUR)}",
    )

    found = answer.json()
    assert (len(found["markers"]), found["truncated"], found["cap"]) == (500, True, 500)
    assert found["markers"][0]["at"][:19] == (T0 + timedelta(seconds=500)).isoformat()[:19]


async def test_another_tenant_s_project_has_no_marks_or_coverage_for_us(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    """AC26's rule on the two new routes."""
    await _deploys(db, globex, [T0])
    for path in ("markers", "coverage"):
        answer = await _get(db, acme, f"/releases/{path}?project_id={globex.project_id}")
        assert answer.status_code == 404, path


# ------------------------------------------------------------ coverage, AC73


async def test_each_service_reads_its_coverage_as_of_a_moment(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC73, FR38: all four values; a service never heard from comes from
    `service_last_seen`; a report 31 days old still counts, with its date; a moment
    before the first report reads "first heard"."""
    now = datetime.now(UTC).replace(microsecond=0)
    month_ago = now - timedelta(days=31)
    await _deploys(db, acme, [month_ago], service="billing", version="b1", state="failed")
    await _deploys(db, acme, [now - HOUR], service="checkout", version="v2")
    db.add_all(
        [
            Release(
                org_id=acme.org_id,
                project_id=acme.project_id,
                service_name="billing",
                version="b1",
                first_reported_at=month_ago,
                last_reported_at=month_ago,
                report_count=1,
                last_state="failed",
            ),
            Release(
                org_id=acme.org_id,
                project_id=acme.project_id,
                service_name="checkout",
                version="v2",
                first_seen_at=now - 2 * HOUR,
                first_reported_at=now - HOUR,
                last_reported_at=now - HOUR,
                report_count=1,
                last_state="finished",
            ),
            Release(
                org_id=acme.org_id,
                project_id=acme.project_id,
                service_name="search",
                version="s1",
                first_seen_at=now - 3 * HOUR,
            ),
            ServiceLastSeen(
                org_id=acme.org_id,
                project_id=acme.project_id,
                service_name="mailer",
                last_seen_at=now,
            ),
        ]
    )
    await db.flush()

    answer = await _get(db, acme, f"/releases/coverage?project_id={acme.project_id}")
    by_service = {row["service_name"]: row for row in answer.json()["services"]}
    assert {name: row["coverage"] for name, row in by_service.items()} == {
        "billing": "deploys_only",
        "checkout": "deploys_and_versions",
        "mailer": "none",
        "search": "versions_only",
    }
    assert by_service["billing"]["last_reported_at"][:19] == month_ago.isoformat()[:19]

    before = await _get(
        db,
        acme,
        f"/releases/coverage?project_id={acme.project_id}&service=checkout"
        f"&at={_iso(now - 5 * HOUR)}",
    )
    (checkout,) = before.json()["services"]
    assert checkout["coverage"] == "none"
    assert checkout["first_heard_at"][:19] == (now - 2 * HOUR).isoformat()[:19]
    assert checkout["last_reported_at"] is None


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


# ===================================================================== Step 4
#
# The whole release page. Rows are written relative to `T0`, a whole hour three to four
# hours ago, so no assertion depends on the minute or second a test runs in.


async def _finding_at(
    db: AsyncSession,  # noqa: F811
    team: Team,
    started_at: datetime,
    *,
    service: str = SERVICE,
    project_id: uuid.UUID | None = None,
) -> Finding:
    # Closed, since one service holds one open finding at a time.
    row = Finding(
        org_id=team.org_id,
        project_id=project_id or team.project_id,
        scope="service",
        service_name=service,
        severity="warning",
        state="closed",
        closed_reason="recovered",
        title=f"{service} is slower than normal",
        signals=[],
        started_at=started_at,
        closed_at=started_at + MINUTE,
    )
    db.add(row)
    await db.flush()
    return row


def write_stats_only(
    client: Client, team: Team, minute: datetime, requests: int, *, service: str = SERVICE
) -> None:
    """Requests `service_stats_1m` counted and `service_versions_1m` did not: a dropped
    version write, as FR28 must catch."""
    client.command(
        f"""
        INSERT INTO service_stats_1m
            (tenant_id, project_id, service_name, minute, request_count, error_count,
             latency_buckets)
        SELECT '{team.org_id}', '{team.project_id}', '{service}',
               toDateTime({int(minute.timestamp())}), {requests}, 0,
               arrayReduce('sumForEachState', [CAST({_buckets(requests, FAST)}, 'Array(UInt32)')])
        """
    )


def write_error(
    client: Client,
    team: Team,
    *,
    at: datetime,
    version: str,
    exception_type: str,
    door: str = "server",
    table: str = "spans",
    environment: str | None = None,
    service: str = SERVICE,
) -> None:
    """One stored exception, shaped as the worker writes it: a span event promoted to
    columns, or a log record carrying its version on the resource."""
    resource: dict[str, Any] = {"service": {"name": service, "version": version}}
    if environment is not None:
        resource["deployment"] = {"environment": {"name": environment}}
    trace, span = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    common = {
        "at": at,
        "tenant": str(team.org_id),
        "project": str(team.project_id),
        "service": service,
        "trace": trace,
        "span": span,
        "type": exception_type,
        "message": f"{exception_type} happened",
        "resource": json.dumps(resource),
        "door": door,
    }
    if table == "spans":
        client.command(
            """
            INSERT INTO spans (timestamp, received_at, retention_days, tenant_id, project_id,
                               trace_id, span_id, service_name, span_name, span_kind,
                               status_code, deployment_version, resource_attrs,
                               exception_type, exception_message, door)
            SELECT %(at)s, %(at)s, 30, %(tenant)s, %(project)s, %(trace)s, %(span)s,
                   %(service)s, 'GET /', 'SERVER', 'ERROR', %(version)s, %(resource)s,
                   %(type)s, %(message)s, %(door)s
            """,
            parameters={**common, "version": version},
        )
        return
    client.command(
        """
        INSERT INTO logs (timestamp, received_at, retention_days, tenant_id, project_id,
                          trace_id, span_id, service_name, severity, severity_number, body,
                          body_structured, resource_attrs, log_attrs, exception_type,
                          exception_message, timestamp_source, door)
        SELECT %(at)s, %(at)s, 30, %(tenant)s, %(project)s, %(trace)s, %(span)s,
               %(service)s, 'ERROR', 17, %(message)s, '{}', %(resource)s, '{}', %(type)s,
               %(message)s, 'event', %(door)s
        """,
        parameters=common,
    )


async def _read(
    db: AsyncSession,  # noqa: F811
    team: Team,
    reader_client: AsyncClient,
    path: str,
    *,
    person: User | None = None,
) -> dict[str, Any]:
    answer = await _get(db, team, path, person=person, repository=_repository(reader_client, team))
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


# ------------------------------------------------- T4.1: AC29, AC36, AC67


async def test_a_replacement_with_no_overlap_reads_before_and_after_with_last_week(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC29, FR25: the previous side is the equal stretch before, and last week's
    column is the whole service over the same 40 minutes a week earlier."""
    week = timedelta(days=7)
    write_versions(
        administrator,
        acme,
        minutes("v0", T0 - week, 40, 50, 1)
        + minutes("v1", T0 - HOUR, 60, 100)
        + minutes("v2", T0, 40, 100),
    )
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 39 * MINUTE)

    found = await _comparison(db, acme, reader_client, seeded)

    (block,) = found["blocks"]
    assert block["method"] == "version_before_after"
    assert block["previous_from"].startswith((T0 - 40 * MINUTE).strftime("%Y-%m-%dT%H:%M"))
    assert block["previous_to"].startswith(T0.strftime("%Y-%m-%dT%H:%M"))
    assert (block["last_week"]["requests"], block["last_week"]["errors"]) == (2000, 40)
    assert (found["incomplete"], found["minutes_differing"]) == (False, 0)
    assert (found["versions_combined"], found["mixed_environments"]) == (False, False)


async def test_one_missing_minute_in_a_24_hour_window_reads_incomplete(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC36, FR28: one minute whose version rows fall short of the service's."""
    start = T0 - 24 * HOUR
    write_versions(
        administrator, acme, minutes("v1", start - HOUR, 60, 20) + minutes("v2", start, 1440, 20)
    )
    write_stats_only(administrator, acme, start + 700 * MINUTE, 5)
    seeded = await _release(db, acme, "v2", start, previous="v1")
    await _settle(db, start + 1439 * MINUTE)

    found = await _comparison(db, acme, reader_client, seeded, query="?window=24")

    assert found["window_hours"] == 24
    assert (found["incomplete"], found["minutes_differing"]) == (True, 1)


async def test_combined_versions_and_two_environments_are_said(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC67: `__other__` in a compared minute marks the comparison inexact (FR17), and
    two environment labels in the service's stored spans warn (FR45)."""
    write_versions(
        administrator,
        acme,
        minutes("v1", T0 - HOUR, 60, 100)
        + minutes("v2", T0, 40, 100)
        + minutes("__other__", T0 + 10 * MINUTE, 1, 3),
    )
    for environment in ("production", "staging"):
        write_error(
            administrator,
            acme,
            at=T0 + 5 * MINUTE,
            version="v2",
            exception_type="Boom",
            environment=environment,
        )
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 39 * MINUTE)

    found = await _comparison(db, acme, reader_client, seeded)

    assert found["versions_combined"] is True
    assert found["mixed_environments"] is True


# ---------------------------------------------- T4.1: AC30, AC32's reasons


async def test_every_reason_is_reachable_with_its_own_answer(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC32, AC30's second half: each of FR21's reasons from its own service."""
    write_versions(administrator, acme, minutes("v1", T0 - HOUR, 100, 50), service="seen-v1")
    write_versions(administrator, acme, minutes("", T0 - HOUR, 100, 50), service="no-version")
    write_versions(administrator, acme, minutes("v1", T0, 60, 50), service="first")
    write_versions(administrator, acme, minutes("v1", T0, 60, 50), service="running")
    await _settle(db, T0 + 59 * MINUTE)
    reported_v21 = await _release(db, acme, "v2.1", T0, service="seen-v1", previous="v1")
    cases = {
        "new_version_not_seen": reported_v21,
        "no_request_traffic": await _release(db, acme, "v2", T0, service="silent"),
        "no_version_no_deploy": await _release(db, acme, "v2", T0, service="no-version"),
        "no_previous_version": await _release(db, acme, "v1", T0, service="first"),
        "already_running": await _release(
            db, acme, "v1", T0, service="running", already_running=True
        ),
        "too_recent": await _release(db, acme, "v3", T0 + 57 * MINUTE, service="seen-v1"),
        "numbers_expired": await _release(db, acme, "v0", T0 - 40 * 24 * HOUR, service="old"),
    }
    for reason, seeded in cases.items():
        found = await _comparison(db, acme, reader_client, seeded)
        assert (found["reason"], found["blocks"]) == (reason, []), reason
    found = await _comparison(db, acme, reader_client, cases["new_version_not_seen"])
    assert found["other_versions"] == ["v1"]
    expired = await _comparison(db, acme, reader_client, cases["numbers_expired"])
    assert expired["numbers_kept_days"] in (30, 35)


# ----------------------------------------------------- T4.1: AC64, FR22


async def test_opened_from_a_finding_the_window_reaches_it_and_says_so(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC64: 5 hours on reads 6 hours; 30 hours on reads 24 and does not reach it; with
    no finding each window reads itself; another project's finding is not found."""
    start = T0 - 30 * HOUR
    write_versions(administrator, acme, minutes("v1", start - HOUR, 60, 50))
    seeded = await _release(db, acme, "v2", start, previous="v1")
    await _settle(db, T0)
    later = await _finding_at(db, acme, start + 5 * HOUR)
    much_later = await _finding_at(db, acme, start + 30 * HOUR)

    near = await _comparison(db, acme, reader_client, seeded, query=f"?finding={later.id}")
    far = await _comparison(db, acme, reader_client, seeded, query=f"?finding={much_later.id}")

    assert (near["window_hours"], near["finding"]["reaches"]) == (6, True)
    assert (far["window_hours"], far["finding"]["reaches"]) == (24, False)
    for hours in (1, 6, 24):
        alone = await _comparison(db, acme, reader_client, seeded, query=f"?window={hours}")
        assert (alone["window_hours"], alone["finding"]) == (hours, None)
    stranger = await _get(
        db,
        acme,
        f"/releases/{seeded.release_id}/comparison?finding={uuid.uuid4()}",
        repository=_repository(reader_client, acme),
    )
    assert stranger.status_code == 404


# ------------------------------------------------------------ T4.1: AC78, FR46


async def _told(
    db: AsyncSession,  # noqa: F811
    team: Team,
    version: str,
    *,
    reported_at: datetime | None = None,
    seen_at: datetime | None = None,
    service: str = SERVICE,
) -> None:
    db.add(
        Release(
            org_id=team.org_id,
            project_id=team.project_id,
            service_name=service,
            version=version,
            first_reported_at=reported_at,
            last_reported_at=reported_at,
            report_count=1 if reported_at else 0,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
    )
    await db.flush()


async def test_ci_and_traffic_naming_versions_differently_is_said_with_the_newest_of_each(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC78: two told-never-seen beside one seen-never-told in 24 hours, on one service;
    one told-never-seen alone does not; outside the 24 hours does not count."""
    now = datetime.now(UTC)
    await _told(db, acme, "1.4.0", reported_at=now - 3 * HOUR)
    await _told(db, acme, "1.5.0", reported_at=now - 2 * HOUR)
    await _told(db, acme, "abc123", seen_at=now - HOUR)
    await _told(db, acme, "2.0.0", reported_at=now - 2 * HOUR, service="payments")
    await _told(db, acme, "def456", seen_at=now - HOUR, service="payments")
    await _told(db, acme, "1.0.0", reported_at=now - 30 * HOUR, service="payments")
    await _told(db, acme, "1.1.0", reported_at=now - 3 * HOUR, service="search")
    await _told(db, acme, "2.2.0", reported_at=now - 2 * HOUR, service="search")
    await _told(db, acme, "ghi789", seen_at=now - 30 * HOUR, service="search")

    answer = await _get(db, acme, f"/releases?project_id={acme.project_id}")

    assert answer.status_code == 200, answer.text
    assert answer.json()["naming_differs"] == [
        {"service_name": SERVICE, "reported_version": "1.5.0", "seen_version": "abc123"}
    ]


# ------------------------------------------------------- T4.3: AC69, FR29


async def test_errors_by_version_read_new_gone_and_in_both_and_leave_the_browser_out(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC69: each kind from a seeded error, spans and log records alike; a browser
    key's exception naming the server version does not list on that release."""
    before, after = T0 - 30 * MINUTE, T0 + 30 * MINUTE
    write_error(administrator, acme, at=before, version="v1", exception_type="InBoth")
    write_error(administrator, acme, at=before, version="v1", exception_type="Gone")
    write_error(administrator, acme, at=after, version="v2", exception_type="InBoth")
    write_error(administrator, acme, at=after, version="v2", exception_type="New", table="logs")
    write_error(
        administrator,
        acme,
        at=after,
        version="v2",
        exception_type="FromAPage",
        door="browser",
        table="logs",
    )
    write_error(administrator, acme, at=after, version="v9", exception_type="OtherVersion")
    seeded = await _release(db, acme, "v2", T0, previous="v1")

    found = await _read(db, acme, reader_client, f"/releases/{seeded.release_id}/errors")

    kinds = {group["exception_type"]: group["kind"] for group in found["groups"]}
    assert kinds == {"InBoth": "in_both", "Gone": "gone_here", "New": "new_here"}
    assert (found["timed_out"], found["aged_out_on"], found["browser_door_read"]) == (
        False,
        None,
        False,
    )


async def test_a_release_with_no_server_traffic_reads_its_browser_errors_labelled(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """FR27: a reported release never seen in server traffic still shows its errors,
    marked as coming from the browser."""
    write_error(
        administrator,
        acme,
        at=T0 + 10 * MINUTE,
        version="v2",
        exception_type="FromAPage",
        door="browser",
        table="logs",
    )
    release = Release(
        org_id=acme.org_id,
        project_id=acme.project_id,
        service_name=SERVICE,
        version="v2",
        first_reported_at=T0,
        last_reported_at=T0,
        report_count=1,
    )
    db.add(release)
    await db.flush()
    period = ReleasePeriod(
        org_id=acme.org_id,
        release_id=release.id,
        project_id=acme.project_id,
        service_name=SERVICE,
        started_at=T0,
        start_basis="reported",
    )
    db.add(period)
    await db.flush()

    found = await _read(db, acme, reader_client, f"/releases/{release.id}/errors")

    (group,) = found["groups"]
    assert (group["exception_type"], group["kind"], group["from_browser"]) == (
        "FromAPage",
        "this_version",
        True,
    )
    assert found["browser_door_read"] is True


async def test_aged_out_errors_give_their_date_and_sampling_errors_off_says_incomplete(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader_client: AsyncClient,
) -> None:
    """AC69's FR30 and FR31: never a list of zeros."""
    old = await _release(db, acme, "v1", T0 - 40 * 24 * HOUR, previous="v0")
    project = await db.get(Project, acme.project_id)
    assert project is not None
    project.sampling_config = {"keep_errors": False}
    await db.flush()

    found = await _read(db, acme, reader_client, f"/releases/{old.release_id}/errors")

    assert found["groups"] == []
    assert found["aged_out_on"] is not None
    assert found["may_be_incomplete"] is True


# ------------------------------------------------------------- T4.3: AC76


async def _deploy(
    db: AsyncSession,  # noqa: F811
    team: Team,
    release_id: uuid.UUID,
    deployed_at: datetime,
    *,
    state: str = "finished",
) -> DeployEvent:
    row = DeployEvent(
        org_id=team.org_id,
        project_id=team.project_id,
        service_name=SERVICE,
        version="v2",
        deployed_at=deployed_at,
        source="github-actions",
        delivery_id=f"run-{uuid.uuid4()}",
        event_metadata={"pipeline": "build 41"},
        state=state,
        commit_sha="a" * 40,
        url="https://ci.example/runs/41",
        release_id=release_id,
    )
    db.add(row)
    await db.flush()
    return row


async def test_sender_text_is_a_person_s_and_its_metadata_a_member_s(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC76, FR54: a token is refused; a viewer reads the link and source, never the
    metadata; `GET /releases/{id}` and `GET /deploys` carry none of it (T5.4)."""
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _deploy(db, acme, seeded.release_id, T0)
    path = f"/releases/{seeded.release_id}/sender-details"

    member = (await _get(db, acme, path, person=acme.member)).json()["deploys"][0]
    viewer = (await _get(db, acme, path, person=acme.viewer)).json()["deploys"][0]
    async with _client(db, acme, None, _NoClickHouse()) as client:
        # No session store is needed: a token is refused before one is asked.
        client._transport.app.dependency_overrides[get_session_store] = lambda: None  # type: ignore[attr-defined]
        as_token = await client.get(
            f"{API_PREFIX}{path}", headers={"Authorization": "Bearer nvt_live_x"}
        )
    release = (await _get(db, acme, f"/releases/{seeded.release_id}")).json()
    listed = (await _get(db, acme, f"/deploys?project_id={acme.project_id}")).json()

    assert (member["url"], member["source"], member["metadata"]) == (
        "https://ci.example/runs/41",
        "github-actions",
        {"pipeline": "build 41"},
    )
    assert (viewer["url"], viewer["metadata"], viewer["metadata_withheld"]) == (
        "https://ci.example/runs/41",
        None,
        True,
    )
    assert as_token.status_code == 403
    assert [deploy["commit_sha"] for deploy in listed["deploys"]] == ["a" * 40]
    for shown in (json.dumps(release), json.dumps(listed)):
        for sender_text in ("ci.example", "github-actions", "build 41"):
            assert sender_text not in shown


async def test_sender_details_of_another_tenant_s_release_are_not_found(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    globex: Team,
) -> None:
    theirs = await _release(db, globex, "v1", T0)
    answer = await _get(db, acme, f"/releases/{theirs.release_id}/sender-details")
    assert answer.status_code == 404


# ------------------------------------------------------- T4.4: AC23, AC41


async def test_a_reported_release_reads_not_seen_then_serving_once_counted(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC23."""
    now = datetime.now(UTC)
    release = Release(
        org_id=acme.org_id,
        project_id=acme.project_id,
        service_name=SERVICE,
        version="v2",
        first_reported_at=now - HOUR,
        last_reported_at=now - HOUR,
        report_count=1,
    )
    db.add(release)
    await db.flush()
    path = f"/releases/{release.id}"

    before = (await _get(db, acme, path)).json()
    release.first_seen_at = now - 50 * MINUTE
    release.last_seen_at = now - MINUTE
    await db.flush()
    after = (await _get(db, acme, path)).json()

    assert (before["activity"], before["basis"]) == ("not_seen", "reported")
    assert (after["activity"], after["basis"]) == ("serving", "both")


async def test_a_release_lists_findings_that_started_while_it_served_and_its_deploys(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC41, FR34: a finding in the period's own minute is after it; one a minute before
    is not; nor one after the period ended, nor another service's. The deploys list the
    sender's time beside our arrival time."""
    started = T0 + 30 * timedelta(seconds=1)
    seeded = await _release(db, acme, "v2", started, previous="v1", ended_at=T0 + 2 * HOUR)
    same_minute = await _finding_at(db, acme, T0)
    later = await _finding_at(db, acme, T0 + HOUR)
    await _finding_at(db, acme, T0 - MINUTE)
    await _finding_at(db, acme, T0 + 3 * HOUR)
    await _finding_at(db, acme, T0 + HOUR, service="payments")
    deploy = await _deploy(db, acme, seeded.release_id, T0 - 20 * MINUTE)

    found = (await _get(db, acme, f"/releases/{seeded.release_id}")).json()

    (period,) = found["periods"]
    assert [row["id"] for row in period["later_findings"]] == [str(same_minute.id), str(later.id)]
    assert period["later_findings_truncated"] is False
    (shown,) = found["deploys"]
    assert (shown["id"], shown["state"], shown["commit_sha"]) == (
        str(deploy.id),
        "finished",
        "a" * 40,
    )
    assert shown["deployed_at"] != shown["received_at"]


# ------------------------------------------------------------- T4.3: AC65


async def test_five_versions_draw_the_top_four_and_the_rest_together(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    administrator: Client,
    reader_client: AsyncClient,
) -> None:
    """AC65, FR26: no version and the worker's fold are labels, never versions."""
    rows = (
        minutes("v1", T0 - HOUR, 90, 50)
        + minutes("v2", T0, 30, 40)
        + minutes("", T0, 30, 30)
        + minutes("__other__", T0, 30, 20)
        + minutes("v3", T0, 30, 2)
        + minutes("v4", T0, 30, 1)
    )
    write_versions(administrator, acme, rows)
    seeded = await _release(db, acme, "v2", T0, previous="v1")
    await _settle(db, T0 + 29 * MINUTE)

    found = await _read(db, acme, reader_client, f"/releases/{seeded.release_id}/rollout")

    assert found["series"] == [
        {"kind": "version", "version": "v1"},
        {"kind": "version", "version": "v2"},
        {"kind": "no_version", "version": None},
        {"kind": "combined", "version": None},
        {"kind": "other_versions", "version": None},
    ]
    first_rollout_minute = next(
        row for row in found["minutes"] if row["minute"].startswith(T0.strftime("%Y-%m-%dT%H:%M"))
    )
    assert first_rollout_minute["requests"] == [50, 40, 30, 20, 3]


class _TimesOut:
    """A ClickHouse read past the account's time limit (TIMEOUT_EXCEEDED, 159)."""

    async def query(self, *_: Any, **__: Any) -> list[Any]:
        from clickhouse_connect.driver.exceptions import DatabaseError

        error = DatabaseError("Timeout exceeded")
        error.code = 159
        raise error


async def test_a_timed_out_errors_read_says_so_and_is_never_an_empty_list(
    db: AsyncSession,  # noqa: F811
    acme: Team,
) -> None:
    """AC68's second half, FR51."""
    seeded = await _release(db, acme, "v2", T0, previous="v1")

    answer = await _get(db, acme, f"/releases/{seeded.release_id}/errors", repository=_TimesOut())

    assert answer.status_code == 200, answer.text
    assert (answer.json()["timed_out"], answer.json()["groups"]) == (True, [])
