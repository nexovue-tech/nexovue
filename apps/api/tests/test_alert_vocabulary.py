"""Migration 0028 froze the alert vocabulary; the models read it live.

The two are meant to be equal on the day 0028 ran, and to drift only through a
later migration. This fails when `nexovue_platform.alerts.model` gains a value
and no migration widens the CHECK constraint that would refuse it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from nexovue_platform.alerts import model

import nexovue_api

MIGRATION = Path(nexovue_api.__file__).parents[2] / "migrations" / "versions" / "0028_alerts.py"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("alerts_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _values(quoted: str) -> tuple[str, ...]:
    return tuple(part.strip().strip("'") for part in quoted.split(","))


@pytest.mark.parametrize(
    ("frozen", "live"),
    [
        ("_CHANNEL_KINDS", model.CHANNEL_KINDS),
        ("_CONDITION_KINDS", model.CONDITION_KINDS),
        ("_SEVERITIES", model.SEVERITIES),
        ("_NOTIFICATION_KINDS", model.NOTIFICATION_KINDS),
        ("_NOTIFICATION_STATES", model.NOTIFICATION_STATES),
        ("_ATTEMPT_OUTCOMES", model.ATTEMPT_OUTCOMES),
        ("_EVALUATION_PHASES", model.EVALUATION_PHASES),
    ],
)
def test_the_migration_allows_exactly_the_vocabulary(frozen: str, live: tuple[str, ...]) -> None:
    assert _values(getattr(_migration(), frozen)) == live


def test_the_backfilled_defaults_are_the_ones_signup_creates() -> None:
    migration = _migration()
    assert migration._OWNERS_CHANNEL_NAME == model.OWNERS_CHANNEL_NAME
    assert migration._DEFAULT_RULE_NAME == model.DEFAULT_RULE_NAME
    assert json.loads(migration._DEFAULT_RULE_CONDITION) == model.DEFAULT_RULE_CONDITION.model_dump(
        mode="json"
    )
