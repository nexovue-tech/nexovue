"""The batch under a slow statement and a client that really leaves (F20, T2.12's seam).

Dev data is small and statements are fixed, so a test fixture puts a slow,
tenant-bound statement in the readers' table in place of the project trend; the app
never does (plan, *The test seam*). Against real Postgres, real ClickHouse and, for the
disconnect, a real uvicorn server:

- **AC19, live, ClickHouse's limit first.** With the statement limit below the batch's
  deadline, ClickHouse answers `TIMEOUT_EXCEEDED` and the panel reads `timed_out`,
  never `refused` or `failed`, and the response is a 200 carrying the panels that read,
  inside the batch's deadline counted from the request's arrival.
- **AC18.** A client that drops mid-batch: scans not started do not start, and every
  cap place is free once the statement ends. What ClickHouse did with the running
  statement is recorded in `system.processes` and printed.
- **AC63.** A statement whose readers have all gone ends on the server within its fixed
  limit plus 1 s, and sooner if `cancel_http_readonly_queries_on_client_close` works.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final

import httpx
import pytest
import uvicorn
from clickhouse_connect.driver.asyncclient import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_dashboard_panels_live import (  # noqa: F401  shared fixtures and helpers
    _app_client,
    _connect,
    _dashboard,
    _panel,
    _seed,
    reader,
)
from test_findings_cases_live import Team, _team, db  # noqa: F401

from nexovue_api.dashboardpanels import scans, service, shared_reads
from nexovue_api.dashboardpanels.caps import DASHBOARD_SCANS_PER_PROCESS
from nexovue_api.dashboardpanels.shared_reads import (
    TIMEOUT_EXCEEDED,
    Answer,
    ReadKey,
    SharedReads,
)
from nexovue_api.dashboards.compile import PROJECT_TREND, ScanKey
from nexovue_api.data.clickhouse.base import TenantScopedRepository, server_error_code
from nexovue_api.ratelimit.policy import PANEL_READ_DEADLINE_SECONDS
from nexovue_api.settings import API_PREFIX

# Half a second a row, one row a block, so ClickHouse checks its limit between rows.
SLOW_SQL: Final = """
SELECT sum(x) AS x
FROM (
    SELECT sleepEachRow(0.5) AS x, {tenant_id:String} AS tenant_id,
           {project_id:String} AS project_id
    FROM numbers({rows:UInt32})
    SETTINGS max_block_size = 1
)
WHERE tenant_id = {tenant_id:String} AND project_id = {project_id:String}
"""
SLOW_ROWS: Final = 120
SHORT_LIMIT_SECONDS: Final = 2
CANCEL_MARGIN_SECONDS: Final = 1.0

PROCESSES_SQL: Final = """
SELECT count() FROM system.processes
WHERE user = 'nexovue_reader'
  AND query LIKE '%sleepEachRow%'
  AND query NOT LIKE '%system.processes%'
"""


@pytest.fixture
async def acme(db: AsyncSession) -> Team:  # noqa: F811
    return await _team(db, f"Seams{uuid.uuid4().hex[:8]}")


@pytest.fixture
def stopped_with() -> list[int | None]:
    """ClickHouse's error code for each slow statement that ended in an error."""
    return []


@pytest.fixture
def slow_reader(monkeypatch: pytest.MonkeyPatch, stopped_with: list[int | None]) -> list[float]:
    """The project trend's reader replaced by the slow statement; each start is recorded,
    and how each that failed was stopped."""
    started: list[float] = []

    async def slow(
        repository: TenantScopedRepository,
        project_id: str,
        scan: ScanKey,
        window_start: datetime,
        window_end: datetime,
    ) -> Answer:
        started.append(time.monotonic())
        try:
            rows = await repository.query(SLOW_SQL, {"project_id": project_id, "rows": SLOW_ROWS})
        except Exception as error:
            stopped_with.append(server_error_code(error))
            raise
        return Answer(value=rows, rows=len(rows), read_at=datetime.now(UTC))

    monkeypatch.setitem(scans.READERS, PROJECT_TREND, slow)
    return started


@pytest.fixture
async def admin() -> AsyncIterator[AsyncClient]:
    client = await _connect(
        os.environ.get(
            "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
        )
    )
    try:
        yield client
    finally:
        await client.close()


async def _running(admin: AsyncClient) -> int:
    result = await admin.query(PROCESSES_SQL)
    return int(result.result_rows[0][0])


async def _until_none_running(admin: AsyncClient, limit_seconds: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < limit_seconds:
        if await _running(admin) == 0:
            return time.monotonic() - started
        await asyncio.sleep(0.2)
    return float("inf")


# --------------------------------------------------------------------- AC19


async def test_ac19_clickhouses_own_limit_first_reads_timed_out_and_the_rest_answer(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,  # noqa: F811
    slow_reader: list[float],
    stopped_with: list[int | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "SHARED_READ_STATEMENT_SECONDS", SHORT_LIMIT_SECONDS)
    dashboard = await _dashboard(
        db,
        acme,
        _panel("p_slow", "project_trend", signal="rate"),
        _panel("p_fast", "service_trend", service="checkout", signal="rate"),
    )
    async with _app_client(db, acme, acme.member, reader) as client:
        # AC19's clock starts at the request's arrival; building the app is the harness's.
        sent = time.monotonic()
        answer = await client.get(
            f"{API_PREFIX}/dashboard-panels/read",
            params={
                "dashboard_id": str(dashboard.id),
                "project_id": str(acme.project_id),
                "range": "1h",
            },
        )
        answered = time.monotonic() - sent

    assert answer.status_code == 200
    panels = answer.json()["panels"]
    assert panels["p_slow"] == {"state": "timed_out", "kind": "project_trend"}
    assert panels["p_fast"]["state"] in {"not_in_project", "empty", "ok"}
    # ClickHouse's own limit fired first: the statement was stopped by TIMEOUT_EXCEEDED
    # before the batch answered, which the batch's deadline (20 s) could not have done,
    # since the reader profile lets a statement run 30 s.
    assert stopped_with == [TIMEOUT_EXCEEDED]
    # AC19's promise: the batch never spends more than its deadline from arrival.
    assert answered < PANEL_READ_DEADLINE_SECONDS, f"answered in {answered:.1f} s"


# ------------------------------------------------------------------- AC18


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_ac18_a_client_that_drops_starts_nothing_more_and_frees_every_place(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    reader: AsyncClient,  # noqa: F811
    admin: AsyncClient,
    slow_reader: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "SHARED_READ_STATEMENT_SECONDS", 6)
    # Six slow scans: one key per panel, through distinct services' trends read slowly.
    monkeypatch.setitem(scans.READERS, "service_trend", scans.READERS[PROJECT_TREND])
    dashboard = await _dashboard(
        db,
        acme,
        *(
            _panel(f"p_{index}", "service_trend", service=f"svc-{index}", signal="rate")
            for index in range(6)
        ),
    )
    client = _app_client(db, acme, acme.member, reader)
    app = client._transport.app  # type: ignore[attr-defined]
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        left_at = time.monotonic() + 1.5
        with pytest.raises(httpx.ReadTimeout):
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as leaving:
                await leaving.get(
                    f"{API_PREFIX}/dashboard-panels/read",
                    params={
                        "dashboard_id": str(dashboard.id),
                        "project_id": str(acme.project_id),
                        "range": "1h",
                    },
                    timeout=1.5,
                )
        running_after_drop = await _running(admin)
        ended_after = await _until_none_running(admin, 6 + CANCEL_MARGIN_SECONDS + 2)
        await asyncio.sleep(0.5)
    finally:
        server.should_exit = True
        await serving

    print(  # noqa: T201
        f"AC18: {len(slow_reader)} of 6 scans started; {running_after_drop} statements still "
        f"running just after the client left; none running {ended_after:.1f} s later"
    )
    # A hosted install holds one organization to 3 of the 5 places, so at most three
    # start, and the rest wait for a place or a permit; none may start once it has gone.
    assert 1 <= len(slow_reader) <= service.PANEL_SCANS_AT_ONCE
    assert all(start < left_at for start in slow_reader), "a scan started after the client left"
    assert ended_after <= 6 + CANCEL_MARGIN_SECONDS
    reads = app.state.dashboard_reads
    # One organization each, so the hosted share does not hide a place still held.
    places = [
        await reads.caps.acquire(str(uuid.uuid4()), raw=False, wait_seconds=0.01)
        for _ in range(DASHBOARD_SCANS_PER_PROCESS)
    ]
    assert all(place is not None for place in places), "a cap place was not given back"


# ------------------------------------------------------------------- AC63


async def test_ac63_a_statement_with_no_reader_left_ends_within_its_limit_or_sooner(
    acme: Team,
    reader: AsyncClient,  # noqa: F811
    admin: AsyncClient,
) -> None:
    limit = 8
    repository = TenantScopedRepository(reader, tenant_id=str(acme.org_id)).bounded_by(limit)
    reads = SharedReads(caps=service.PanelReads.for_install(shared_by_organizations=False).caps)

    async def slow() -> Answer:
        rows = await repository.query(
            SLOW_SQL, {"project_id": str(acme.project_id), "rows": SLOW_ROWS}
        )
        return Answer(value=rows, rows=len(rows), read_at=datetime.now(UTC))

    class Free:
        async def claim(self) -> None:
            return None

    key = ReadKey(
        org_id=str(acme.org_id),
        project_id=str(acme.project_id),
        scan=ScanKey(reader=PROJECT_TREND),
        window_start=datetime.now(UTC),
        window_end=datetime.now(UTC),
    )
    only_reader = asyncio.create_task(
        reads.read(key, run=slow, raw=False, charge=Free(), deadline=reads.now() + 30)
    )
    await asyncio.sleep(1.5)
    assert await _running(admin) == 1, "the slow statement never reached ClickHouse"

    only_reader.cancel()
    ended_after = await _until_none_running(admin, limit + CANCEL_MARGIN_SECONDS + 2)

    print(f"AC63: the statement ended {ended_after:.1f} s after its last reader left")  # noqa: T201
    assert ended_after <= limit + CANCEL_MARGIN_SECONDS
    # The app's own limit is the fixed 15 s this test shortened to 8 to finish sooner.
    assert shared_reads.SHARED_READ_STATEMENT_SECONDS == 15
