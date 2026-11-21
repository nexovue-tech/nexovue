"""From a fixture case to an error group a finding links, on real ClickHouse (F5 plan
T2.7).

Seeds each case's spans under a tenant of its own, then reads them back the two ways
Nexovue does: F3's new-error read, as the evidence job runs it, and its link of a group
to a finding on that service; and the API's error group read, whose stack F5's path
matching starts from (Step 3). Each language's stack must come back naming the
fixture's files in its own form. **These are seeded shapes**: a match rate measured on
them says nothing about real stacks until real ones are read (T6.4).

This suite fails rather than skips when ClickHouse is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import clickhouse_connect
import pytest
from clickhouse_cleanup import delete_after_run
from clickhouse_connect.driver.asyncclient import AsyncClient
from live import require_stack

from fix.fixtures import Case, load_cases
from fix.seed_findings import seed, stacktrace
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.errorgroups import service as error_groups
from nexovue_scheduler.evidence.rules import ERROR_LEAD_IN
from nexovue_scheduler.evidence.store import connect_store, groups_for

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
CASES: Final = load_cases()
COUNT: Final = 3
# The newest occurrence, a minute before the read, so it lies inside the read window.
END: Final = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0)
# A finding that started as the first occurrence did, as F3 reads its window.
FINDING_START: Final = END - timedelta(minutes=COUNT - 1)


def _reachable() -> bool:
    url = urlsplit(CLICKHOUSE_DSN)
    try:
        with socket.create_connection((url.hostname or "localhost", url.port or 18123), 1.0):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="tools/ai-eval/fix/test_seed_findings_live.py",
    remedy="ClickHouse is not reachable — run `just up`",
)


@pytest.fixture(scope="module")
def tenant() -> tuple[str, str]:
    org_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    delete_after_run("spans", org_id)
    written = seed(
        CASES, dsn=CLICKHOUSE_DSN, org_id=org_id, project_id=project_id, end=END, count=COUNT
    )
    assert written == COUNT * len(CASES)
    return org_id, project_id


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    url = urlsplit(READER_DSN)
    made = await clickhouse_connect.get_async_client(
        host=url.hostname or "localhost",
        port=url.port or 18123,
        username=url.username or "",
        password=url.password or "",
        database=(url.path or "/").lstrip("/"),
    )
    try:
        yield made
    finally:
        await made.close()


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_f3_reads_a_new_error_group_and_links_it_to_the_service(
    case: Case, tenant: tuple[str, str]
) -> None:
    reader = connect_store(READER_DSN).for_project(*tenant)
    window_from = (FINDING_START - ERROR_LEAD_IN).replace(second=0)
    groups = reader.new_error_groups(window_from=window_from, window_to=END + timedelta(minutes=1))

    linked = groups_for(groups, [case.service.name])
    mine = [g for g in linked if g.count == COUNT and case.service.name in g.services]
    by_type = [g for g in mine if g.exception_type in (case.exception_type, None)]
    assert by_type, f"no group linked for {case.id}"


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
async def test_the_api_reads_back_the_stack_naming_the_fixture_files(
    case: Case, tenant: tuple[str, str], client: AsyncClient
) -> None:
    org_id, project_id = tenant
    repository = TenantScopedRepository(client, tenant_id=org_id)
    start, end = END - timedelta(hours=1), END + timedelta(minutes=2)
    listed = await error_groups.list_error_groups(
        repository,
        project_id=project_id,
        service_name=case.service.name,
        from_time=start,
        to_time=end,
        limit=50,
    )
    [group] = [
        g
        for g in listed.groups
        if g.exception_type == case.exception_type
        and case.message.split(" ")[0] in g.message_template
    ]
    detail = await error_groups.get_error_group(
        repository, project_id=project_id, group_id=group.group_id, from_time=start, to_time=end
    )

    stack = detail.recent[0].stacktrace
    assert stack == stacktrace(case)
    for frame in case.frames:
        assert frame.runtime_path in stack and str(frame.line) in stack


def test_each_language_prints_its_own_form() -> None:
    forms: dict[str, str] = {}
    for case in CASES:
        forms.setdefault(case.service.language, stacktrace(case))

    [two_frames] = [c for c in CASES if c.id == "py_stack_average"]
    printed = stacktrace(two_frames).splitlines()
    # Python prints the innermost frame last, just above the exception.
    assert two_frames.frames[0].runtime_path in printed[-3]
    assert printed[-1] == f"{two_frames.exception_type}: {two_frames.message}"
    assert forms["python"].startswith('Traceback (most recent call last):\n  File "/app/')
    assert "    at " in forms["typescript"] and "/app/dist/" in forms["typescript"]
    assert "\tat com.acme.billing." in forms["java"] and ".java:" in forms["java"]
