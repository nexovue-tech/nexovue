"""Migration 0084 froze Step 8's write failures exactly, and the model holds the same words and
columns (F5 plan T8.1, T8.2).

0084 holds a frozen copy of `nexovue_platform.fixes.records.WRITE_FAILURES`, so a later change
is a migration of its own; this holds it equal to the list today, and the model's CHECKs to
the same words. Run CI's and the poller's columns are the record, never stored code (BR9).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Final

from nexovue_platform.fixes import records, statements
from sqlalchemy import CheckConstraint, Table

import nexovue_api
from nexovue_api.data.postgres.models import FindingFix

MIGRATION: Final = (
    Path(nexovue_api.__file__).parents[2] / "migrations/versions/0084_ai_solution_follow.py"
)
STEP_8_COLUMNS: Final = (
    "ci_parent_sha",
    "ci_commit_requested_at",
    "ci_pushed_at",
    "pull_request_state_at",
    "pull_request_merge_commit_sha",
    "pull_request_polled_at",
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MIGRATION.stem, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _words(frozen: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([A-Za-z0-9_]+)'", frozen))


def _table() -> Table:
    table = FindingFix.__table__
    assert isinstance(table, Table)
    return table


def _check(name: str) -> str:
    [found] = [
        each
        for each in _table().constraints
        if isinstance(each, CheckConstraint) and each.name == f"ck_finding_fixes_{name}"
    ]
    return str(found.sqltext)


def test_0084_follows_0083() -> None:
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0084", "0083")


def test_0084_froze_exactly_the_shared_write_failures() -> None:
    assert _words(_migration()._WRITE_FAILURES) == records.WRITE_FAILURES


def test_the_model_checks_the_same_write_failures() -> None:
    assert _words(_check("last_write_failure_is_known")) == records.WRITE_FAILURES


def test_step_8_s_new_codes_are_run_ci_s_two() -> None:
    assert set(records.WRITE_FAILURES) - set(records.STEP_7_WRITE_FAILURES) == {
        records.BRANCH_CHANGED,
        records.PULL_REQUEST_NOT_OPEN,
    }


def test_the_model_has_step_8_s_columns_and_checks_their_shas() -> None:
    assert set(STEP_8_COLUMNS) <= set(_table().columns.keys())
    shas = _check("followed_commits_are_shas")
    assert "ci_parent_sha" in shas and "pull_request_merge_commit_sha" in shas


def test_br9_step_8_s_columns_are_the_record_not_stored_code() -> None:
    assert set(STEP_8_COLUMNS).isdisjoint(statements.STORED_CODE_COLUMNS)
