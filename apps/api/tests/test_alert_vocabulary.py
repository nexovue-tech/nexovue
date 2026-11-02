"""Migrations freeze the alert and detection vocabulary; the models read it live.

Each frozen constant is read from the latest migration that defines it, because
a CHECK widened later is spelled again in the migration that widens it. This
fails when `nexovue_platform.alerts.model` or `nexovue_platform.detection` gains
a value and no migration widens the CHECK constraint that would refuse it.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from nexovue_platform import detection
from nexovue_platform.alerts import model

import nexovue_api

VERSIONS: Final = Path(nexovue_api.__file__).parents[2] / "migrations" / "versions"


def _defines(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(target, ast.Name) and target.id == name
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
    )


def _latest_definition(name: str) -> ModuleType:
    """The newest migration assigning `name` at module level, loaded."""
    defining = [path for path in sorted(VERSIONS.glob("[0-9]*_*.py")) if _defines(path, name)]
    assert defining, f"no migration freezes {name}"
    path = defining[-1]
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frozen(name: str) -> object:
    return getattr(_latest_definition(name), name)


def _values(quoted: str) -> tuple[str, ...]:
    return tuple(part.strip().strip("'") for part in quoted.split(","))


@pytest.mark.parametrize(
    ("frozen", "live"),
    [
        ("_CHANNEL_KINDS", model.CHANNEL_KINDS),
        ("_CONDITION_KINDS", model.CONDITION_KINDS),
        ("_SEVERITIES", model.SEVERITIES),
        ("_NOTIFICATION_KINDS", model.NOTIFICATION_KINDS),
        ("_PROJECT_NOTIFICATION_KINDS", model.PROJECT_NOTIFICATION_KINDS),
        ("_NOTIFICATION_STATES", model.NOTIFICATION_STATES),
        ("_ATTEMPT_OUTCOMES", model.ATTEMPT_OUTCOMES),
        ("_ATTEMPT_KINDS", model.ATTEMPT_KINDS),
        ("_EVALUATION_PHASES", model.EVALUATION_PHASES),
        ("_FINDING_SCOPES", detection.FINDING_SCOPES),
        ("_FINDING_STATES", detection.FINDING_STATES),
        ("_CLOSED_REASONS", detection.CLOSED_REASONS),
        ("_SIGNALS", detection.SIGNALS),
        ("_SHIFT_REASONS", detection.SHIFT_REASONS),
        ("_SENSITIVITIES", detection.SENSITIVITIES),
        ("_INTAKE_REFUSAL_REASONS", detection.INTAKE_REFUSAL_REASONS),
    ],
)
def test_the_latest_migration_allows_exactly_the_vocabulary(
    frozen: str, live: tuple[str, ...]
) -> None:
    quoted = _frozen(frozen)
    assert isinstance(quoted, str)
    assert _values(quoted) == live


def test_the_frozen_detection_defaults_are_the_models() -> None:
    assert _frozen("_DEFAULT_SENSITIVITY") == detection.DEFAULT_SENSITIVITY
    assert _frozen("_DEFAULT_TIMEZONE") == detection.DEFAULT_TIMEZONE


def test_the_backfilled_defaults_are_the_ones_signup_creates() -> None:
    assert _frozen("_OWNERS_CHANNEL_NAME") == model.OWNERS_CHANNEL_NAME
    assert _frozen("_DEFAULT_RULE_NAME") == model.DEFAULT_RULE_NAME
    condition = _frozen("_DEFAULT_RULE_CONDITION")
    assert isinstance(condition, str)
    assert json.loads(condition) == model.DEFAULT_RULE_CONDITION.model_dump(mode="json")
