"""`/releases` without stores (F28 plan T1.14): the words a row's activity and basis
take, the window and the page link, no sender text in any response, and 0054's frozen
copies held to the platform's names."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import environment  # noqa: F401  settles the settings before the app is imported
import pytest
from nexovue_platform import releases as platform

from nexovue_api.data.postgres.models import NAME_MAX_LENGTH
from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.releases import service
from nexovue_api.releases.schemas import (
    BlockView,
    ComparisonView,
    ReadingView,
    ReleaseRow,
    ReleaseView,
    SideView,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0054_releases.py"
SENDER_TEXT = {"metadata", "url", "source", "delivery_id"}


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("revision_0054", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def test_the_migration_s_frozen_copies_match_the_platform() -> None:
    migration = _migration()
    assert _quoted(platform.DEPLOY_STATES) == migration._DEPLOY_STATES
    assert _quoted(platform.START_BASES) == migration._START_BASES
    assert _quoted((platform.NO_VERSION, platform.OTHER_VERSION)) == migration._NOT_A_RELEASE
    assert migration._NAME_LONGEST == NAME_MAX_LENGTH == platform.NAME_MAX_LENGTH


@pytest.mark.parametrize(
    ("first_seen", "last_seen", "ended", "activity"),
    [
        (None, None, False, "not_seen"),
        (NOW - timedelta(days=1), NOW - timedelta(minutes=2), True, "replaced"),
        (NOW - timedelta(days=1), NOW - timedelta(minutes=9), False, "serving"),
        (NOW - timedelta(days=1), NOW - timedelta(minutes=11), False, "idle"),
    ],
)
def test_a_release_s_activity(
    first_seen: datetime | None, last_seen: datetime | None, ended: bool, activity: str
) -> None:
    """Spec, *A release's activity*: counted in the last 10 minutes is serving."""
    assert (
        service.activity_of(
            first_seen_at=first_seen, last_seen_at=last_seen, latest_period_ended=ended, now=NOW
        )
        == activity
    )


def test_a_release_s_basis_keeps_reported_and_seen_apart() -> None:
    """BR3."""
    assert service.basis_of(NOW, None) == "seen"
    assert service.basis_of(None, NOW) == "reported"
    assert service.basis_of(NOW, NOW) == "both"


def test_the_windows_offered_are_1_6_and_24_hours() -> None:
    """FR22."""
    assert service.window_hours_of(None) == 1
    assert [service.window_hours_of(hours) for hours in (1, 6, 24)] == [1, 6, 24]
    with pytest.raises(InvalidRequestError):
        service.window_hours_of(2)


def test_a_page_link_reads_back_and_a_forged_one_is_refused() -> None:
    class _Row:
        started_at = NOW
        period_id = uuid.uuid4()

    cursor = service._cursor_of(_Row())
    assert service._read_cursor(cursor) == (NOW, _Row.period_id)
    for forged in ("not-a-cursor", "eHl6"):
        with pytest.raises(InvalidRequestError):
            service._read_cursor(forged)


@pytest.mark.parametrize("model", [ReleaseRow, ReleaseView, ComparisonView, BlockView, SideView])
def test_no_response_carries_sender_text(model: Any) -> None:
    """FR54, AC76: sender text is `sender-details`' alone."""
    assert not set(model.model_fields) & SENDER_TEXT


def test_a_block_says_from_and_to_on_the_wire() -> None:
    """The design's response shape."""
    side = SideView(version="v2", requests=10, errors=0, p95_ms=None)
    block = BlockView(
        kind="rollout",
        method="side_by_side",
        start=NOW,
        end=NOW + timedelta(minutes=3),
        minutes_compared=3,
        this_version=side,
        previous_version=side,
        failure_share=ReadingView(reading="not_clearly_different"),
        p95=ReadingView(reading=None, note="warm_up"),
    )
    shown = block.model_dump(mode="json", by_alias=True)
    assert {"from", "to"} <= set(shown)
    assert "start" not in shown
