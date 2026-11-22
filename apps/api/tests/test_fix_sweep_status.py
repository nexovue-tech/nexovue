"""Migration 0086 froze the one sweep name `fix_sweep_status` holds, and the model checks the same
name (F5 plan T9.1; open decision 70)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Final

from nexovue_platform.fixes.records import FIX_SWEEP_NAME
from sqlalchemy import CheckConstraint, Table

import nexovue_api
from nexovue_api.data.postgres.models import FixSweepStatus
from nexovue_api.organizations.tables import EXCLUDED_TABLES

MIGRATION: Final = (
    Path(nexovue_api.__file__).parents[2] / "migrations/versions/0086_ai_solution_sweep_finished.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MIGRATION.stem, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0086_follows_0085() -> None:
    assert _migration().down_revision == "0085"


def test_0086_froze_the_sweep_s_name() -> None:
    assert _migration()._SWEEP == FIX_SWEEP_NAME


def test_the_model_checks_the_same_name() -> None:
    table = FixSweepStatus.__table__
    assert isinstance(table, Table)
    [check] = [each for each in table.constraints if isinstance(each, CheckConstraint)]
    assert str(check.sqltext) == f"sweep = '{FIX_SWEEP_NAME}'"


def test_the_table_is_install_wide_bookkeeping_in_the_inventory() -> None:
    assert "fix_sweep_status" in EXCLUDED_TABLES
