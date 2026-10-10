"""What the nightly erasure refuses, before anything reaches ClickHouse (ADR-014).

The many-tenant statements are the one place nexovue can delete more than one
customer's telemetry in a statement, so what may reach them is narrow: a
statement that binds the owed tenants, a list that is not empty, and a run only
in the hour the deployment chose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Never

import pytest

from nexovue_scheduler.erasure import run
from nexovue_scheduler.erasure.owed import OwedTenants
from nexovue_scheduler.erasure.store import ErasureRefusedError, ErasureStore, _checked

OWED = OwedTenants(ids=("0190f3a2-0000-7000-8000-000000000001",))


class _ClientThatMustNotBeReached:
    def command(self, *args: Any, **kwargs: Any) -> Never:
        raise AssertionError("a refused erasure reached the store")

    def query(self, *args: Any, **kwargs: Any) -> Never:
        raise AssertionError("a refused erasure reached the store")


def _store() -> ErasureStore:
    return ErasureStore(_ClientThatMustNotBeReached())  # type: ignore[arg-type]


def test_an_empty_list_is_refused_rather_than_certifying_nothing() -> None:
    with pytest.raises(ErasureRefusedError):
        _store().submit("spans", OwedTenants(ids=()))
    with pytest.raises(ErasureRefusedError):
        _store().remaining(OwedTenants(ids=()))


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE spans DELETE WHERE 1",
        "ALTER TABLE spans DELETE WHERE tenant_id = {tenant_id:String}",
        "/* tenant_id IN {owed_tenants:Array(String)} */ ALTER TABLE spans DELETE WHERE 1",
        "ALTER TABLE spans DELETE WHERE 'tenant_id IN {owed_tenants:Array(String)}' != ''",
    ],
)
def test_a_statement_that_does_not_bind_the_owed_tenants_is_refused(statement: str) -> None:
    with pytest.raises(ErasureRefusedError):
        _checked(statement, OWED)


def test_without_an_eraser_credential_the_task_says_so_every_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[str] = []
    monkeypatch.setattr(run, "get_settings", lambda: _Settings(eraser_dsn=""))
    monkeypatch.setattr(run.logger, "error", lambda event, **_: errors.append(event))
    monkeypatch.setattr(run, "with_engine", _never)

    run.erase_owed_tonight()

    assert errors and "CLICKHOUSE_ERASER_DSN" in errors[0]


def test_outside_the_chosen_hour_the_task_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    other_hour = (datetime.now(UTC).hour + 1) % 24
    monkeypatch.setattr(
        run, "get_settings", lambda: _Settings(eraser_dsn="clickhouse://x@h/db", hour=other_hour)
    )
    monkeypatch.setattr(run, "with_engine", _never)

    run.erase_owed_tonight()


class _Settings:
    def __init__(self, *, eraser_dsn: str, hour: int = 2) -> None:
        self.clickhouse_eraser_dsn = eraser_dsn
        self.erasure_hour_utc = hour


def _never(*_: Any) -> Never:
    raise AssertionError("the task touched a store when it should not have run")
