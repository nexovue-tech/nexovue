"""F4: the closed lists the API, the AI role and migration 0073 share are one list, and the job
the API adds is one the AI role's task takes (as `test_ai_vocabulary.py` does for F3)."""

from __future__ import annotations

import importlib.util
import inspect
import re
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from nexovue_platform import assistant_records as records

from nexovue_scheduler.insight.assistant import answer_question
from nexovue_scheduler.jobs import registry

MIGRATION: Final = (
    Path(__file__).resolve().parents[2] / "api" / "migrations" / "versions" / "0073_assistant.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("assistant_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quoted(frozen: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([a-z_]+)'", frozen))


@pytest.mark.parametrize(
    ("frozen", "shared"),
    [
        ("_STATES", records.MESSAGE_STATES),
        ("_FINAL_STATES", records.FINAL_STATES),
        ("_IN_FLIGHT", records.IN_FLIGHT),
        ("_PHASES", records.PHASES),
        ("_FAILURES", records.FAILURE_REASONS),
        ("_REJECTIONS", records.REJECTION_REASONS),
        ("_ABSTAINS", records.ABSTAIN_REASONS),
        ("_WRONGS", records.WRONG_REASONS),
        ("_STEP_STATES", records.STEP_STATES),
    ],
)
def test_the_migration_holds_the_same_closed_lists(frozen: str, shared: tuple[str, ...]) -> None:
    assert _quoted(getattr(_migration(), frozen)) == shared


def test_the_question_job_carries_exactly_what_its_task_takes() -> None:
    request = records.answer_question_request(message_id="m", org_id="o", call_seconds=30)

    assert set(request.kwargs) == set(inspect.signature(answer_question).parameters)
    assert request.task in registry.registered(registry.TASK_SETS["insight"])
    assert request.queue == records.ASSISTANT_QUEUE and request.max_attempts == 1
    # Limits outlast the whole clock, so the job is never killed before the deadline.
    _, deadline, hard_end = records.clocks(datetime(2026, 11, 11, tzinfo=UTC), 30)
    assert request.hard_limit_seconds >= (hard_end - deadline).total_seconds()
    assert request.soft_limit_seconds < request.hard_limit_seconds
