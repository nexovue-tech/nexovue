"""Migration 0081 froze Step 4's widened lists exactly (F5 plan T4.4, T4.12).

0081 holds frozen copies of `nexovue_platform.fixes.records`' reason, retry and check-run
lists, so a later change to one is a migration of its own; this holds them equal to the
lists today, and the model's CHECKs to the same words.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from nexovue_platform.fixes import records
from sqlalchemy import CheckConstraint, Table

import nexovue_api
from nexovue_api.data.postgres.models import FindingFix, FindingFixCheck

MIGRATION: Final = (
    Path(nexovue_api.__file__).parents[2] / "migrations/versions/0081_ai_solution_suggestion.py"
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
        ("_FIX_REASONS", records.STEP_4_FIX_REASONS),
        ("_RETRY_CODES", records.RETRY_CODES),
        ("_CHECK_REASONS", records.STEP_4_FIX_CHECK_REASONS),
    ],
)
def test_0081_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(), frozen)) == shared


def test_0081_follows_0080() -> None:
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0081", "0080")


def _check(model: type, name: str) -> str:
    table = model.__table__  # type: ignore[attr-defined]
    assert isinstance(table, Table)
    [found] = [
        each
        for each in table.constraints
        if isinstance(each, CheckConstraint) and each.name == f"ck_{table.name}_{name}"
    ]
    return str(found.sqltext)


def test_the_models_check_the_same_words() -> None:
    assert _words(_check(FindingFix, "reason_is_known")) == records.FIX_REASONS
    assert _words(_check(FindingFix, "retry_code_is_known")) == records.RETRY_CODES
    assert _words(_check(FindingFixCheck, "reason_is_known")) == records.FIX_CHECK_REASONS


def test_step_4_s_codes_are_new_to_0081() -> None:
    assert set(records.PROPOSAL_REASONS).isdisjoint(records.STEP_3_FIX_REASONS)
    assert len(records.WITHHELD_RULES) == 10


def test_the_check_run_s_roles_are_the_action_table_s() -> None:
    """The check run reads a person's role again (design, *The check run*, step 1); its copy
    of who may ask must be the API's own table for `REQUEST_FIX`."""
    from nexovue_api.auth.permissions import Action, may
    from nexovue_api.data.postgres.models import USER_ROLES

    asking = {role for role in USER_ROLES if may(role, Action.REQUEST_FIX)}
    assert set(records.ROLES_WHO_ASK) == asking
