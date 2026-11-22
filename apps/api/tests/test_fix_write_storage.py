"""Migration 0083 froze Step 7's lists exactly, and the model holds the same words (F5 plan
T7.1; ADR-100).

0083 holds frozen copies of `nexovue_platform.fixes.records`' state, check-reason, pull
request state and write-failure lists, so a later change to one is a migration of its own;
this holds them equal to the lists today, and the model's CHECKs and the one-draft index to
the same words. The note and the approved digest are stored code (BR9); the pull request's
facts are the record.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from nexovue_platform.fixes import records, statements
from sqlalchemy import CheckConstraint, Index, Table

import nexovue_api
from nexovue_api.data.postgres.models import FindingFix
from nexovue_api.organizations.tables import REMOVED_BY_CASCADE

MIGRATION: Final = (
    Path(nexovue_api.__file__).parents[2] / "migrations/versions/0083_ai_solution_write.py"
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


@pytest.mark.parametrize(
    ("frozen", "shared"),
    [
        ("_FIX_STATES", records.FIX_STATES),
        ("_CHECK_REASONS", records.FIX_CHECK_REASONS),
        ("_PULL_REQUEST_STATES", records.PULL_REQUEST_STATES),
        ("_WRITE_FAILURES", records.WRITE_FAILURES),
    ],
)
def test_0083_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(), frozen)) == shared


def test_0083_follows_0082() -> None:
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0083", "0082")


def test_the_model_checks_the_same_words() -> None:
    assert _words(_check("state_is_known")) == records.FIX_STATES
    assert _words(_check("pull_request_state_is_known")) == records.PULL_REQUEST_STATES
    assert _words(_check("last_write_failure_is_known")) == records.WRITE_FAILURES


def test_the_branch_check_names_only_the_suggestion_s_own_branch() -> None:
    """FR23 in the store: a stored branch can only be `nexovue/suggestion-<id>`."""
    assert records.FIX_BRANCH_PREFIX in _check("branch_is_its_own")
    assert _migration()._BRANCH_PREFIX == records.FIX_BRANCH_PREFIX
    assert records.fix_branch("x") == "nexovue/suggestion-x"


def test_br8_the_model_and_the_migration_hold_one_draft_by_one_condition() -> None:
    [index] = [
        each
        for each in _table().indexes
        if isinstance(each, Index) and each.name == "uq_finding_fixes_one_draft"
    ]
    where = index.dialect_options["postgresql"]["where"]
    assert index.unique
    assert " ".join(str(where).split()) == " ".join(_migration()._ONE_DRAFT.split())


def test_step_7_s_new_codes_are_new_to_0083() -> None:
    assert set(records.FIX_CHECK_REASONS) - set(records.STEP_5_FIX_CHECK_REASONS) == set(
        records.DRAFT_REFUSALS
    )
    assert set(records.FIX_STATES) - set(records.STEP_3_FIX_STATES) == {
        records.APPROVED,
        records.OPENED,
    }


def test_br9_the_note_and_the_digest_are_stored_code_and_the_pull_request_is_the_record() -> None:
    stored = set(statements.STORED_CODE_COLUMNS)
    assert {"requester_note", "approved_digest"} <= stored
    assert {
        "pull_request_number",
        "pull_request_url",
        "pull_request_branch",
        "pull_request_state",
        "pull_request_commit_sha",
        "approved_by",
        "approved_at",
    }.isdisjoint(stored)


def test_the_settings_go_with_their_repository() -> None:
    assert REMOVED_BY_CASCADE["fix_repository_settings"] == "connection_repositories"
