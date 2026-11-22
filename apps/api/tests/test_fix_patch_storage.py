"""Migration 0082 froze Step 5's widened lists exactly (F5 plan T5.1 to T5.3; ADR-094).

0082 holds frozen copies of `nexovue_platform.fixes.records`' reason, code-deleted, check-run
and verdict lists, so a later change to one is a migration of its own; this holds them
equal to the lists today, and the model's CHECKs to the same words.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from nexovue_platform.fixes import records, statements
from sqlalchemy import CheckConstraint, Table

import nexovue_api
from nexovue_api.data.postgres.models import ConnectionRepository, FindingFix

MIGRATION: Final = (
    Path(nexovue_api.__file__).parents[2] / "migrations/versions/0082_ai_solution_patch.py"
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
        ("_FIX_REASONS", records.FIX_REASONS),
        ("_CODE_DELETED_REASONS", records.CODE_DELETED_REASONS),
        ("_CHECK_REASONS", records.STEP_5_FIX_CHECK_REASONS),
        ("_VERDICTS", records.FIX_VERDICTS),
    ],
)
def test_0082_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(), frozen)) == shared


def test_0082_follows_0081() -> None:
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0082", "0081")


def _check(name: str) -> str:
    table = FindingFix.__table__
    assert isinstance(table, Table)
    [found] = [
        each
        for each in table.constraints
        if isinstance(each, CheckConstraint) and each.name == f"ck_finding_fixes_{name}"
    ]
    return str(found.sqltext)


def test_the_model_checks_the_same_verdicts_and_deleted_reasons() -> None:
    assert _words(_check("verdict_is_known")) == records.FIX_VERDICTS
    assert _words(_check("code_deleted_reason_is_known")) == records.CODE_DELETED_REASONS


def test_step_5_s_codes_are_new_to_0082() -> None:
    assert set(records.TAKEN_BACK_REASONS).isdisjoint(records.STEP_4_FIX_REASONS)
    assert records.PROJECT_DELETED not in records.STEP_3_CODE_DELETED_REASONS
    assert set(records.STEP_5_FIX_CHECK_REASONS) - set(records.STEP_4_FIX_CHECK_REASONS) == {
        *records.STALE_REASONS,
        records.CHECKS_REFUSED_AT_CLICK,
    }


def test_a_verdict_and_the_access_clock_are_not_stored_code() -> None:
    """BR9 deletes code and keeps the record: a verdict and the clock are the record."""
    stored = set(statements.STORED_CODE_COLUMNS)
    assert {"verdict", "verdict_by", "verdict_at"}.isdisjoint(stored)
    repositories = ConnectionRepository.__table__
    assert isinstance(repositories, Table)
    assert "unreachable_since" in repositories.columns
