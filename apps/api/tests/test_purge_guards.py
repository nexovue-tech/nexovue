"""What the purge refuses, before anything reaches ClickHouse.

The read path's worst failure is showing one customer another's data. The
purge's worst failure is `ALTER TABLE spans DELETE` without a predicate, which
empties the store for everybody — so the refusals are tested against a client
that fails the test if it is called at all, rather than against a server that
would have to survive the mistake to report it.

Offline. `test_purge_live.py` is where rows actually move.
"""

from __future__ import annotations

from typing import Any, Never

import pytest

from nexovue_api.data.clickhouse.base import (
    SystemTableOnlyError,
    TenantFilterMissingError,
    TenantScopedPurger,
)
from nexovue_api.purge import (
    InvalidTenantError,
    PurgeFailedError,
    PurgeUnfinishedError,
    TenantPurge,
)

TENANT = "01a07b34-78cf-7b87-8761-642ab07d0304"


class ClientThatMustNotBeReached:
    """Any call is the failure under test, so every entry point raises."""

    database = "nexovue"

    async def command(self, *args: Any, **kwargs: Any) -> Never:
        raise AssertionError(f"a refused statement reached the server: {args} {kwargs}")

    async def query(self, *args: Any, **kwargs: Any) -> Never:
        raise AssertionError(f"a refused statement reached the server: {args} {kwargs}")


def _purger() -> TenantScopedPurger:
    return TenantScopedPurger(ClientThatMustNotBeReached(), TENANT)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE spans DELETE WHERE 1",
        "ALTER TABLE spans DELETE WHERE tenant_id != ''",
        # The predicate present, but only where ClickHouse will not act on it.
        "ALTER TABLE spans DELETE WHERE 1 -- tenant_id = {tenant_id:String}",
        "ALTER TABLE spans DELETE WHERE '{tenant_id:String}' != ''",
    ],
)
async def test_a_deletion_without_a_real_tenant_predicate_is_refused(statement: str) -> None:
    """Each of these would delete every tenant's rows if it ran."""
    with pytest.raises(TenantFilterMissingError):
        await _purger().delete(statement)


async def test_an_unscoped_read_may_only_name_a_system_table() -> None:
    """The metadata door must not become a second way to read telemetry."""
    with pytest.raises(SystemTableOnlyError):
        await _purger().query_server_metadata("SELECT count() FROM spans", {})


async def test_a_scoped_read_without_the_predicate_is_refused() -> None:
    with pytest.raises(TenantFilterMissingError):
        await _purger().query("SELECT count() FROM spans")


@pytest.mark.parametrize("tenant_id", ["", "all", "*", "01a07b34-78cf", "'; DROP"])
def test_a_purge_of_something_that_is_not_an_organization_is_refused(tenant_id: str) -> None:
    """A malformed id that matched nothing would report a deletion that did nothing."""
    with pytest.raises(InvalidTenantError):
        TenantPurge(TenantScopedPurger(ClientThatMustNotBeReached(), tenant_id))  # type: ignore[arg-type]


def test_a_purge_of_a_real_organization_id_is_accepted() -> None:
    """The refusal above must not be refusing everything."""
    assert TenantPurge(_purger()).tenant_id == TENANT


# ------------------------------------------- what a stuck purge reports back
#
# A live store cannot be made to kill a mutation or fail one on demand, so the
# two branches that decide "keep waiting" from "give up" are driven by a stub
# that answers the two statements the service sends.


class StoreThatNeverFinishes:
    """One table keeps its rows, and `system.mutations` says why."""

    database = "nexovue"

    def __init__(self, *, failure_reason: str = "", killed: bool = False) -> None:
        self._failure_reason = failure_reason
        self._killed = killed

    async def command(self, *args: Any, **kwargs: Any) -> None:
        """Every deletion is accepted; none of them finish."""

    async def query(self, sql: str, **kwargs: Any) -> Any:
        if "system.mutations" in sql:
            return _Result(
                [
                    "table",
                    "mutation_id",
                    "is_done",
                    "is_killed",
                    "parts_to_do",
                    "latest_fail_reason",
                ],
                [["spans", "mutation_1.txt", 0, int(self._killed), 1, self._failure_reason]],
            )
        return _Result(["table", "remaining"], [["spans", 7]])


class _Result:
    def __init__(self, column_names: list[str], result_rows: list[list[Any]]) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


def _purge_of(store: StoreThatNeverFinishes) -> TenantPurge:
    return TenantPurge(TenantScopedPurger(store, TENANT))  # type: ignore[arg-type]


async def test_a_deadline_reports_the_rows_left_and_what_the_store_last_said() -> None:
    """ "Timed out" alone leaves the next person with nowhere to look."""
    purge = _purge_of(StoreThatNeverFinishes(failure_reason="Memory limit exceeded"))

    with pytest.raises(PurgeUnfinishedError) as unfinished:
        await purge.wait_until_erased(deadline_seconds=0)

    assert unfinished.value.remaining == {"spans": 7}
    assert unfinished.value.last_errors == {"spans": "Memory limit exceeded"}
    assert "Memory limit exceeded" in str(unfinished.value)


async def test_a_mutation_that_merely_failed_is_waited_on_rather_than_abandoned() -> None:
    """ClickHouse retries a failed mutation, so a reason is not yet a verdict.

    A store briefly out of memory records one and then succeeds — which is what
    the dev stack did while this was being written.
    """
    purge = _purge_of(StoreThatNeverFinishes(failure_reason="Memory limit exceeded"))

    # Not PurgeFailedError: the deadline is what ends this, not the reason.
    with pytest.raises(PurgeUnfinishedError):
        await purge.wait_until_erased(deadline_seconds=0)


async def test_a_killed_mutation_is_reported_as_a_failure_immediately() -> None:
    """Killed is the one state no amount of waiting resolves."""
    purge = _purge_of(StoreThatNeverFinishes(killed=True))

    with pytest.raises(PurgeFailedError) as failed:
        await purge.wait_until_erased(deadline_seconds=60)

    assert failed.value.failures == {"spans": "killed"}
