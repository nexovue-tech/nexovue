"""A broken connector says so (P1-37 follow-up).

Before this, `last_sync_at` and `status` were never written and
`assume_customer_role` had no error handling. A customer whose trust policy did
not name our account got a task that threw every few minutes forever and nothing
they could see — an empty graph, indistinguishable from having nothing to report.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from nexovue_scheduler.pollers import health, integrations


class RecordingConnection:
    """Captures the statement and its parameters instead of running them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> None:
        self.calls.append((str(statement), parameters))


class RecordingEngine:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    def begin(self) -> Any:
        engine = self

        class _Transaction:
            async def __aenter__(self) -> RecordingConnection:
                return engine.connection

            async def __aexit__(self, *_: object) -> None:
                return None

        return _Transaction()


async def test_a_successful_poll_records_when_it_last_worked() -> None:
    engine, integration_id = RecordingEngine(), uuid.uuid4()

    await integrations.record_poll_succeeded(engine, integration_id)  # type: ignore[arg-type]

    statement, parameters = engine.connection.calls[0]
    assert "last_sync_at = now()" in statement
    assert parameters["status"] == integrations.STATUS_ACTIVE
    assert parameters["id"] == integration_id


async def test_a_failed_poll_records_why() -> None:
    """The reason is the point. AWS names what went wrong, and the customer is
    the only person who can fix it."""
    engine, integration_id = RecordingEngine(), uuid.uuid4()

    await integrations.record_poll_failed(
        engine,  # type: ignore[arg-type]
        integration_id,
        "AccessDenied: not authorized to perform sts:AssumeRole",
    )

    _, parameters = engine.connection.calls[0]
    assert parameters["status"] == integrations.STATUS_FAILING
    assert "sts:AssumeRole" in parameters["reason"]


async def test_a_hostile_error_cannot_fill_the_column() -> None:
    engine = RecordingEngine()

    await integrations.record_poll_failed(engine, uuid.uuid4(), "x" * 10_000)  # type: ignore[arg-type]

    _, parameters = engine.connection.calls[0]
    assert len(parameters["reason"]) == integrations.MAX_ERROR_LENGTH


def test_failing_to_record_health_does_not_fail_the_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising here would turn "we could not update a status column" into "this
    poll failed", which is a worse lie than the one it corrects."""

    def _explode(*_: object, **__: object) -> None:
        raise RuntimeError("postgres is unreachable")

    monkeypatch.setattr(health, "create_async_engine", _explode)

    health.report_success(_settings(), str(uuid.uuid4()))  # must not raise


def _settings() -> Any:
    class _S:
        postgres_dsn = "postgresql+asyncpg://u:p@localhost:5432/x"

    return _S()
