"""Migration 0077 froze rung 2b's words exactly (F5 plan T2b.1).

The migration holds frozen copies of `nexovue_platform.fixes.records`' lists as they stood
for rung 2b, so a later change to a list is a migration of its own (0079 widened them for
Step 3, held by `test_fix_source_storage.py`); this holds 0077's copies equal to rung 2b's
words, and the model's checks to today's.
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
from nexovue_api.data.postgres.models import FindingFix

MIGRATION: Final = (
    Path(nexovue_api.__file__).parents[2] / "migrations/versions/0077_ai_solution_general.py"
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
        ("_FIX_KINDS", (records.KIND_GENERAL,)),
        ("_FIX_STATES", (records.PROPOSING, records.PROPOSED, records.REFUSED, records.FAILED)),
        ("_GENERAL_LANGUAGES", records.GENERAL_LANGUAGES),
        ("_FIX_REASONS", records.GENERAL_REASONS),
        ("_RETRY_CODES", records.GENERAL_RETRY_CODES),
    ],
)
def test_0077_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(), frozen)) == shared


def test_0077_froze_the_suggestion_s_byte_cap() -> None:
    assert _migration()._GENERAL_SUGGESTION_MAX_BYTES == records.GENERAL_SUGGESTION_MAX_BYTES


def test_the_model_checks_the_same_words() -> None:
    table = FindingFix.__table__
    assert isinstance(table, Table)
    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    def check(name: str) -> str:
        [text] = [
            sql
            for constraint, sql in checks.items()
            if constraint.split("finding_fixes_")[-1] == name
        ]
        return text

    assert _words(check("reason_is_known")) == records.FIX_REASONS
    assert _words(check("state_is_known")) == records.FIX_STATES
    assert _words(check("kind_is_known")) == records.FIX_KINDS
