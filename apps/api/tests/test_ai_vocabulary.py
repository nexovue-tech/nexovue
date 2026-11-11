"""Migration 0070's frozen copies of the AI closed lists equal the code's (F3, AC96).

A `CHECK` built from a list that has since moved would refuse a value the code writes, or let
through one it no longer means; a later change to a list is a migration of its own.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from nexovue_platform import ai_records as ai

from nexovue_api.data.postgres.models import JOB_RESULT_KINDS

VERSIONS: Final = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _migration() -> ModuleType:
    [path] = VERSIONS.glob("0070_*.py")
    spec = importlib.util.spec_from_file_location("revision_0070", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


@pytest.mark.parametrize(
    ("frozen", "current"),
    [
        ("_WIRE_FORMATS", ai.WIRE_FORMATS),
        ("_OFF_REASONS", ai.OFF_REASONS),
        ("_RUN_PURPOSES", ai.RUN_PURPOSES),
        ("_RUN_OUTCOMES", ai.RUN_OUTCOMES),
        ("_JOB_RESULT_KINDS", JOB_RESULT_KINDS),
    ],
)
def test_each_frozen_list_is_the_code_s(frozen: str, current: tuple[str, ...]) -> None:
    assert getattr(_migration(), frozen) == _quoted(current)


def test_the_call_time_range_is_the_code_s() -> None:
    source = (next(VERSIONS.glob("0070_*.py"))).read_text(encoding="utf-8")
    assert f"call_seconds BETWEEN {ai.CALL_SECONDS_MIN} AND {ai.CALL_SECONDS_MAX}" in source
    assert f'server_default="{ai.CALL_SECONDS_DEFAULT}"' in source
