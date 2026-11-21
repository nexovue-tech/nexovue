"""Migration 0079 froze F5's shared lists exactly (F5 plan T3.1).

0079 holds frozen copies of `nexovue_platform.fixes.records`' lists, so a later change to
one is a migration of its own; this holds them equal to the lists today. The deletion
statement names only columns and tables that exist after 0079 (T2.5's promise kept).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from nexovue_platform.fixes import records, statements
from sqlalchemy import Table

import nexovue_api
from nexovue_api.data.postgres.models import FindingFix

MIGRATION: Final = (
    Path(nexovue_api.__file__).parents[2] / "migrations/versions/0079_ai_solution_source.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MIGRATION.stem, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _words(frozen: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([A-Za-z0-9_]+)'", frozen))


@pytest.mark.parametrize(
    ("frozen", "shared"),
    [
        ("_FIX_KINDS", records.FIX_KINDS),
        ("_FIX_STATES", records.FIX_STATES),
        ("_IN_FLIGHT", records.IN_FLIGHT_STATES),
        ("_WITH_A_REASON", records.STATES_WITH_A_REASON),
        ("_FIX_REASONS", records.STEP_3_FIX_REASONS),
        ("_BASES", records.BASES),
        ("_CANDIDATE_SOURCES", records.CANDIDATE_SOURCES),
        ("_CODE_DELETED_REASONS", records.CODE_DELETED_REASONS),
        ("_CHECK_PURPOSES", records.FIX_CHECK_PURPOSES),
        ("_CHECK_STATUSES", records.FIX_CHECK_STATUSES),
        ("_LINK_STATUSES", records.GITHUB_LINK_STATUSES),
        ("_LINK_REASONS", records.GITHUB_LINK_REASONS),
        ("_CONSENT_WITHDRAWN_REASONS", records.CONSENT_WITHDRAWN_REASONS),
    ],
)
def test_0079_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(), frozen)) == shared


def test_0079_froze_the_caps_and_the_more_files_job() -> None:
    migration = _migration()

    assert migration._HINT_MAX_CHARACTERS == records.HINT_MAX_CHARACTERS
    assert migration._MAX_ROUNDS == records.MAX_ROUNDS
    assert f"'{records.BUILD_SOURCE_PACK_TASK}'" in migration._REQUEST_MORE_FILES
    assert f"'{records.INTERACTIVE_QUEUE}'" in migration._REQUEST_MORE_FILES


def test_clear_stored_code_names_only_what_0079_made() -> None:
    table = FindingFix.__table__
    assert isinstance(table, Table)

    assert set(statements.STORED_CODE_COLUMNS) <= set(table.columns.keys())
    assert set(statements.STORED_CODE_TABLES) <= set(FindingFix.metadata.tables)
