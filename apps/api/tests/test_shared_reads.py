"""Sharing one scan's answer between people, for 30 seconds, inside one process (F20, T2.11).

30 people at 30 scans a minute is 900 against an organization's 600, so nothing else
meets NFR12 (D27). The rules are the design's table, *The rules for a joined read*:
only a request holding its cap place and its charge creates the in-flight entry, so a
refusal is never shared (AC87); ten misses run one statement and charge one reader
(AC86); the statement's limit is fixed, so a joiner never inherits a leader's deadline
(AC88); the task carries on for those still waiting and is cancelled when nobody is
(AC89); the store is bounded by rows, with expiry (AC90); and a kept answer is served
only to its own tenant and project, with the time it was really read (AC62). With under
1 s left no scan starts, and ClickHouse's own timeout reads `timed_out` (AC92).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from nexovue_api.dashboardpanels import shared_reads
from nexovue_api.dashboardpanels.caps import ScanCaps
from nexovue_api.dashboardpanels.shared_reads import (
    Answer,
    FailedRead,
    ReadKey,
    RefusedRead,
    Served,
    SharedReads,
    TimedOutRead,
)
from nexovue_api.dashboards.compile import SERVICE_TREND, ScanKey
from nexovue_api.ratelimit.charge import Refused

WINDOW_END: Final = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
PLENTY: Final = 10.0


class _Charge:
    """A scan charge that counts its claims, and refuses when told to."""

    def __init__(self, refuse: bool = False) -> None:
        self.claims = 0
        self._refuse = refuse

    async def claim(self) -> Refused | None:
        self.claims += 1
        return Refused(policy="telemetry", retry_after_seconds=20) if self._refuse else None


class _Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return asyncio.get_running_loop().time() + self.offset


def _key(org: str = "org-a", project: str = "project-1", service: str = "checkout") -> ReadKey:
    return ReadKey(
        org_id=org,
        project_id=project,
        scan=ScanKey(reader=SERVICE_TREND, service=service),
        window_start=WINDOW_END - timedelta(hours=1),
        window_end=WINDOW_END,
    )


def _reads(clock: _Clock | None = None, caps: ScanCaps | None = None) -> SharedReads:
    return SharedReads(
        caps=caps or ScanCaps(shared_by_organizations=False), clock=clock or _Clock()
    )


def _deadline(reads: SharedReads, seconds: float = PLENTY) -> float:
    return reads.now() + seconds


class _Statement:
    """A statement that counts its runs and finishes when released."""

    def __init__(self, rows: int = 60, hold: bool = True) -> None:
        self.runs = 0
        self.cancelled = 0
        self.release = asyncio.Event()
        if not hold:
            self.release.set()
        self._rows = rows

    async def __call__(self) -> Answer:
        self.runs += 1
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return Answer(value=["point"] * self._rows, rows=self._rows, read_at=datetime.now(UTC))


def _failing(error: BaseException) -> Callable[[], Awaitable[Answer]]:
    async def run() -> Answer:
        raise error

    return run


# --------------------------------------------------------------------- AC86


async def test_ac86_ten_concurrent_misses_run_one_statement_and_charge_one_reader() -> None:
    reads = _reads()
    statement = _Statement()
    charges = [_Charge() for _ in range(10)]

    waiting = [
        asyncio.create_task(
            reads.read(_key(), run=statement, raw=False, charge=charge, deadline=_deadline(reads))
        )
        for charge in charges
    ]
    await asyncio.sleep(0.05)
    statement.release.set()
    outcomes = await asyncio.gather(*waiting)

    assert statement.runs == 1
    assert sum(charge.claims for charge in charges) == 1
    assert all(isinstance(outcome, Served) for outcome in outcomes)
    assert sorted(outcome.source for outcome in outcomes if isinstance(outcome, Served)) == [
        *["joined"] * 9,
        "read",
    ]


# ----------------------------------------------------------------- AC62, AC90


async def test_ac62_a_second_reader_spends_no_scan_and_sees_when_it_was_read() -> None:
    reads = _reads()
    first = _Statement(hold=False)
    served = await reads.read(
        _key(), run=first, raw=False, charge=_Charge(), deadline=_deadline(reads)
    )
    second_charge = _Charge()

    again = await reads.read(
        _key(),
        run=_Statement(hold=False),
        raw=False,
        charge=second_charge,
        deadline=_deadline(reads),
    )

    assert isinstance(served, Served) and isinstance(again, Served)
    assert again.source == "kept"
    assert second_charge.claims == 0
    assert again.answer.read_at == served.answer.read_at


@pytest.mark.parametrize(
    "other", [_key(org="org-b"), _key(project="project-2"), _key(service="payment")]
)
async def test_ac62_no_reader_gets_another_tenants_projects_or_services_kept_answer(
    other: ReadKey,
) -> None:
    reads = _reads()
    await reads.read(
        _key(), run=_Statement(hold=False), raw=False, charge=_Charge(), deadline=_deadline(reads)
    )
    statement = _Statement(hold=False)

    outcome = await reads.read(
        other, run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads)
    )

    assert isinstance(outcome, Served) and outcome.source == "read"
    assert statement.runs == 1


async def test_ac90_an_expired_answer_goes_without_waiting_for_eviction() -> None:
    clock = _Clock()
    reads = _reads(clock)
    await reads.read(
        _key(), run=_Statement(hold=False), raw=False, charge=_Charge(), deadline=_deadline(reads)
    )
    assert reads.rows_kept() == 60

    clock.offset += shared_reads.SHARED_READ_SECONDS + 1
    statement = _Statement(hold=False)
    outcome = await reads.read(
        _key(), run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads)
    )

    assert isinstance(outcome, Served) and outcome.source == "read"
    assert statement.runs == 1


async def test_ac90_an_answer_over_2000_rows_is_not_kept() -> None:
    reads = _reads()
    await reads.read(
        _key(),
        run=_Statement(rows=shared_reads.SHARED_READ_ANSWER_ROWS + 1, hold=False),
        raw=False,
        charge=_Charge(),
        deadline=_deadline(reads),
    )

    assert reads.rows_kept() == 0


async def test_ac90_the_store_keeps_at_most_its_rows_in_all_and_per_organization() -> None:
    reads = _reads()
    per_answer = shared_reads.SHARED_READ_ANSWER_ROWS
    for org_index in range(8):
        for service_index in range(shared_reads.SHARED_READ_ORGANIZATION_ROWS // per_answer + 2):
            await reads.read(
                _key(org=f"org-{org_index}", service=f"svc-{service_index}"),
                run=_Statement(rows=per_answer, hold=False),
                raw=False,
                charge=_Charge(),
                deadline=_deadline(reads),
            )
            assert reads.rows_kept() <= shared_reads.SHARED_READ_MAX_ROWS
            assert reads.rows_kept(f"org-{org_index}") <= shared_reads.SHARED_READ_ORGANIZATION_ROWS

    assert reads.rows_kept() >= shared_reads.SHARED_READ_MAX_ROWS - per_answer


async def test_a_failure_is_never_kept() -> None:
    reads = _reads()
    await reads.read(
        _key(),
        run=_failing(RuntimeError("boom")),
        raw=False,
        charge=_Charge(),
        deadline=_deadline(reads),
    )
    statement = _Statement(hold=False)

    outcome = await reads.read(
        _key(), run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads)
    )

    assert isinstance(outcome, Served) and outcome.source == "read"


# --------------------------------------------------------------------- AC87


async def test_ac87_a_leader_refused_on_quota_shares_no_refusal() -> None:
    reads = _reads()
    statement = _Statement(hold=False)

    refused = await reads.read(
        _key(), run=statement, raw=False, charge=_Charge(refuse=True), deadline=_deadline(reads)
    )
    joiner = await reads.read(
        _key(), run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads)
    )

    assert isinstance(refused, RefusedRead) and refused.retry_after_seconds == 20
    assert isinstance(joiner, Served) and joiner.source == "read"
    assert statement.runs == 1


async def test_ac87_a_leader_refused_on_the_process_cap_creates_no_entry_and_charges_nothing() -> (
    None
):
    caps = ScanCaps(shared_by_organizations=False)
    held = [await caps.acquire("someone", raw=False, wait_seconds=0.01) for _ in range(5)]
    reads = _reads(caps=caps)
    charge = _Charge()

    outcome = await reads.read(
        _key(), run=_Statement(hold=False), raw=False, charge=charge, deadline=_deadline(reads)
    )

    assert isinstance(outcome, RefusedRead)
    assert charge.claims == 0
    assert all(place is not None for place in held)


# --------------------------------------------------------------------- AC88


async def test_ac88_a_joiner_never_inherits_the_leaders_deadline() -> None:
    reads = _reads()
    statement = _Statement()
    leader = asyncio.create_task(
        reads.read(
            _key(), run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads, 1.2)
        )
    )
    await asyncio.sleep(0.01)
    joiner = asyncio.create_task(
        reads.read(_key(), run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads, 5))
    )

    assert isinstance(await leader, TimedOutRead)
    statement.release.set()
    served = await joiner

    assert isinstance(served, Served) and served.source == "joined"
    assert statement.runs == 1


# --------------------------------------------------------------------- AC89


async def test_ac89_a_leader_that_disconnects_leaves_its_joiner_an_answer() -> None:
    reads = _reads()
    statement = _Statement()
    leader = asyncio.create_task(
        reads.read(_key(), run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads))
    )
    await asyncio.sleep(0.01)
    joiner = asyncio.create_task(
        reads.read(_key(), run=statement, raw=False, charge=_Charge(), deadline=_deadline(reads))
    )
    await asyncio.sleep(0.01)

    leader.cancel()
    statement.release.set()

    served = await joiner
    assert isinstance(served, Served)
    assert statement.cancelled == 0


async def test_ac89_with_no_reader_left_the_statement_is_cancelled_and_its_place_freed() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    reads = _reads(caps=caps)
    statement = _Statement()
    only = asyncio.create_task(
        reads.read(_key(), run=statement, raw=True, charge=_Charge(), deadline=_deadline(reads))
    )
    await asyncio.sleep(0.01)

    only.cancel()
    await asyncio.sleep(0.01)

    assert statement.cancelled == 1
    assert [await caps.acquire("x", raw=True, wait_seconds=0.01) for _ in range(2)] != [None, None]


async def test_ac89_a_task_holds_its_place_until_its_statement_ends() -> None:
    caps = ScanCaps(shared_by_organizations=False)
    reads = _reads(caps=caps)
    statement = _Statement()
    leader = asyncio.create_task(
        reads.read(
            _key(), run=statement, raw=True, charge=_Charge(), deadline=_deadline(reads, 1.1)
        )
    )
    joiner = asyncio.create_task(
        reads.read(_key(), run=statement, raw=True, charge=_Charge(), deadline=_deadline(reads))
    )
    assert isinstance(await leader, TimedOutRead)

    # The leader's request has its answer; the statement still runs for the joiner.
    held = await caps.acquire("x", raw=True, wait_seconds=0.01)
    assert held is not None
    assert await caps.acquire("x", raw=True, wait_seconds=0.01) is None

    statement.release.set()
    assert isinstance(await joiner, Served)
    assert await caps.acquire("x", raw=True, wait_seconds=0.01) is not None


async def test_a_joiner_whose_leader_failed_runs_its_own_single_attempt() -> None:
    reads = _reads()
    release = asyncio.Event()
    runs = 0

    async def flaky() -> Answer:
        nonlocal runs
        runs += 1
        await release.wait()
        if runs == 1:
            raise RuntimeError("the first read failed")
        return Answer(value=[], rows=0, read_at=datetime.now(UTC))

    leader_charge, joiner_charge = _Charge(), _Charge()
    leader = asyncio.create_task(
        reads.read(_key(), run=flaky, raw=False, charge=leader_charge, deadline=_deadline(reads))
    )
    await asyncio.sleep(0.01)
    joiner = asyncio.create_task(
        reads.read(_key(), run=flaky, raw=False, charge=joiner_charge, deadline=_deadline(reads))
    )
    await asyncio.sleep(0.01)
    release.set()

    assert isinstance(await leader, FailedRead)
    assert isinstance(await joiner, Served)
    assert runs == 2
    assert (leader_charge.claims, joiner_charge.claims) == (1, 1)


# --------------------------------------------------------------------- AC92


async def test_ac92_with_under_a_second_left_no_scan_starts() -> None:
    reads = _reads()
    statement = _Statement(hold=False)
    charge = _Charge()

    outcome = await reads.read(
        _key(), run=statement, raw=False, charge=charge, deadline=_deadline(reads, 0.9)
    )

    assert isinstance(outcome, TimedOutRead)
    assert statement.runs == 0 and charge.claims == 0


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        (159, TimedOutRead),
        (202, RefusedRead),
        (241, FailedRead),
        (60, FailedRead),
        (None, FailedRead),
    ],
    ids=[
        "TIMEOUT_EXCEEDED",
        "TOO_MANY_SIMULTANEOUS_QUERIES",
        "MEMORY_LIMIT_EXCEEDED",
        "other",
        "no-code",
    ],
)
async def test_ac92_clickhouses_errors_map_to_panel_states_ahead_of_the_broad_catch(
    code: int | None, outcome: type
) -> None:
    reads = _reads()

    result = await reads.read(
        _key(),
        run=_failing(DatabaseError(f"Code: {code}", code=code)),
        raw=False,
        charge=_Charge(),
        deadline=_deadline(reads),
    )

    assert isinstance(result, outcome)


async def test_any_other_error_is_failed_at_the_panel_boundary() -> None:
    reads = _reads()

    result = await reads.read(
        _key(),
        run=_failing(KeyError("bucket")),
        raw=False,
        charge=_Charge(),
        deadline=_deadline(reads),
    )

    assert isinstance(result, FailedRead)


def test_the_store_key_names_tenant_project_scan_and_window() -> None:
    key = _key()

    assert {key, _key()} == {key}
    assert key != ReadKey(
        org_id=key.org_id,
        project_id=key.project_id,
        scan=key.scan,
        window_start=key.window_start,
        window_end=key.window_end + timedelta(minutes=1),
    )
