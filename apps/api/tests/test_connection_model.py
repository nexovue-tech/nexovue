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
from nexovue_api.data.postgres.models import Connection, FindingLink

VERSIONS = Path(nexovue_api.__file__).parents[2] / "migrations" / "versions"
MIGRATION = VERSIONS / "0055_connections.py"
LINKS_MIGRATION = VERSIONS / "0061_finding_links.py"


def _migration(path: Path = MIGRATION) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
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


# ------------------------------------------------------------ finding_links (T4.2)


@pytest.mark.parametrize(
    ("frozen", "shared"),
    [
        ("_ROLES", connections.LINK_ROLES),
        ("_STATUSES", connections.LINK_STATUSES),
        ("_ISSUE_STATES", connections.ISSUE_STATES),
        ("_COMMENTS", connections.COMMENT_KINDS),
        ("_FAILURES", connections.LINK_FAILURES),
    ],
)
def test_the_links_migration_froze_exactly_the_shared_words(
    frozen: str, shared: tuple[str, ...]
) -> None:
    assert _words(getattr(_migration(LINKS_MIGRATION), frozen)) == shared


def test_the_link_model_checks_the_same_words() -> None:
    table = FindingLink.__table__
    assert isinstance(table, Table)
    checks = {
        str(constraint.name).rsplit("finding_links_", 1)[-1]: str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    assert _words(checks["role_is_known"]) == connections.LINK_ROLES
    assert _words(checks["status_is_known"]) == connections.LINK_STATUSES
    assert _words(checks["comment_is_known"]) == connections.COMMENT_KINDS
    assert _words(checks["failure_is_known"]) == connections.LINK_FAILURES


@pytest.mark.parametrize("key", ["acme/api#412", "a/b.c-d_e#1", "OPS-12", "PROJ_2-9"])
def test_an_issue_key_passes_the_frozen_pattern(key: str) -> None:
    pattern = _migration(LINKS_MIGRATION)._EXTERNAL_KEY
    assert re.fullmatch(pattern.strip("^$"), key)
    assert connections.is_issue_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "acme/api",
        "acme/api#0",
        "acme/api#12 ",
        "https://github.com/acme/api/issues/1",
        "acme/api#1/../2",
        "OPS-1/../../myself",
        "ops-1",
        "acme/../api#1",
    ],
)
def test_an_address_or_path_is_not_an_issue_key(key: str) -> None:
    assert not connections.is_issue_key(key)
