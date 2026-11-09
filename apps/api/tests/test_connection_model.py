"""The connection tables' frozen words match the shared vocabulary (F22, T3.4).

Migration 0055 freezes the kinds, states and failure words as literal checks, as the
alert vocabulary is frozen; `nexovue_platform.connections` is what the API and the
scheduler write. A word added to one and not the other is a write the database
refuses at 3 a.m., so the two are held equal here, and the model's checks with them.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from nexovue_platform import connections
from sqlalchemy import Table

import nexovue_api
from nexovue_api.data.postgres.models import Connection

MIGRATION = (
    Path(nexovue_api.__file__).parents[2] / "migrations" / "versions" / "0055_connections.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("connections_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _words(frozen: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([a-z_]+)'", frozen))


@pytest.mark.parametrize(
    ("frozen", "shared"),
    [
        ("_KINDS", connections.CONNECTION_KINDS),
        ("_STATES", connections.CONNECTION_STATES),
        ("_FAILURES", connections.CONNECTION_FAILURES),
    ],
)
def test_the_migration_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(), frozen)) == shared


def test_the_model_checks_the_same_words() -> None:
    table = Connection.__table__
    assert isinstance(table, Table)
    checks = {
        str(constraint.name).rsplit("connections_", 1)[-1]: str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    assert _words(checks["kind_is_known"]) == connections.CONNECTION_KINDS
    assert _words(checks["state_is_known"]) == connections.CONNECTION_STATES
    assert _words(checks["failure_is_known"]) == connections.CONNECTION_FAILURES


@pytest.mark.parametrize("key", ["acme/api", "OPS"])
def test_a_strict_place_key_passes_the_frozen_pattern(key: str) -> None:
    assert re.fullmatch(_migration()._PLACE_KEY.strip("^$"), key)


@pytest.mark.parametrize("key", ["acme", "a b/c", "evil.example/../x", "ops", "OPS-1"])
def test_a_free_text_or_issue_key_is_not_a_place(key: str) -> None:
    assert not re.fullmatch(_migration()._PLACE_KEY.strip("^$"), key)
