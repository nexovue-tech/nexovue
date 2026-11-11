"""The explanation table's frozen words match the shared evidence contract (F3, T1.1).

Migration 0066 freezes the wording states and reasons as literal checks;
`nexovue_platform.evidence` is what the scheduler and the API write. A word added to one
and not the other is a write the database refuses, so the two are held equal here, and
the model's checks with them (AC96).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import get_args

import pytest
from nexovue_platform import evidence
from sqlalchemy import Table

import nexovue_api
from nexovue_api.data.postgres.models import (
    ExplanationStoppedRule,
    FindingExplanation,
    FindingExplanationRating,
)
from nexovue_api.findings.schemas import RatingReason, RuleName

VERSIONS = Path(nexovue_api.__file__).parents[2] / "migrations/versions"
MIGRATION = VERSIONS / "0066_finding_explanations.py"
# Step 2's: ratings' reasons and the rules an organization may stop (FR20, FR54).
STEP_2_MIGRATION = VERSIONS / "0067_explained_ratings_and_stopped_rules.py"


def _migration(path: Path = MIGRATION) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _words(frozen: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([a-z_]+)'", frozen))


@pytest.mark.parametrize(
    ("frozen", "shared"),
    [("_WORDING_STATES", evidence.WORDING_STATES), ("_WORDING_REASONS", evidence.WORDING_REASONS)],
)
def test_the_migration_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(), frozen)) == shared


@pytest.mark.parametrize(
    ("frozen", "shared"),
    [("_RATING_REASONS", evidence.RATING_REASONS), ("_CAUSE_RULES", evidence.CAUSE_RULES)],
)
def test_step_2_froze_exactly_the_shared_words(frozen: str, shared: tuple[str, ...]) -> None:
    assert _words(getattr(_migration(STEP_2_MIGRATION), frozen)) == shared


def test_the_rating_and_rule_models_check_the_same_words() -> None:
    ratings = FindingExplanationRating.__table__
    stopped = ExplanationStoppedRule.__table__
    assert isinstance(ratings, Table) and isinstance(stopped, Table)

    def check(table: Table, name: str) -> str:
        return next(
            str(constraint.sqltext)
            for constraint in table.constraints
            if str(constraint.name).endswith(name) and hasattr(constraint, "sqltext")
        )

    assert _words(check(ratings, "reason_is_known")) == evidence.RATING_REASONS
    assert _words(check(stopped, "rule_is_known")) == evidence.CAUSE_RULES


def test_the_model_checks_the_same_words() -> None:
    table = FindingExplanation.__table__
    assert isinstance(table, Table)
    checks = {
        str(constraint.name).rsplit("finding_explanations_", 1)[-1]: str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    assert _words(checks["wording_state_is_known"]) == evidence.WORDING_STATES
    assert _words(checks["wording_reason_is_known"]).count("died") == 1
    assert _words(checks["wording_reason_is_known"]) == evidence.WORDING_REASONS


def test_the_api_s_closed_lists_are_the_shared_ones() -> None:
    """A rule the API refuses, or a reason it accepts that the database refuses, would be
    a 500 on `/settings/explanations` or a rating (FR20, FR54)."""
    assert get_args(RuleName) == evidence.CAUSE_RULES
    assert get_args(RatingReason) == evidence.RATING_REASONS
