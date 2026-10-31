"""Findings' mapping, offline (F6, T2c.5): what a stored row becomes on the wire.

The feed's order and the scope checks are SQL and are proved against Postgres in
`test_findings_api_live.py`; this suite holds the pure parts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from nexovue_platform import detection
from nexovue_platform.alerts.model import SEVERITIES
from pydantic import ValidationError

from nexovue_api.data.postgres.models import Finding as FindingRow
from nexovue_api.findings import schemas
from nexovue_api.findings.service import (
    SIGNAL_UNITS,
    false_finding_rate,
    parse_signals,
    to_finding,
    trend_values,
    view_state,
)

STARTED: datetime = datetime(2026, 10, 30, 14, 2, tzinfo=UTC)
SLOWER: dict[str, Any] = {
    "signal": "slower",
    "normal": 110.0,
    "current": 330.0,
    "worst": 412.5,
    "comparison": "Tuesdays around 15:00, last 3 weeks",
    "endpoint": None,
}
FAILING: dict[str, Any] = {
    "signal": "failing",
    "normal": 0.4,
    "current": 3.2,
    "worst": None,
    "comparison": "Weekdays around 14:00, last 3 weeks",
    "endpoint": "POST /checkout",
}


def _reading(**fields: Any) -> dict[str, Any]:
    return {
        "window_minutes": 5,
        "window_requests": 600,
        "window_errors": 6,
        "window_p95_ms": 250.0,
    } | fields


# ------------------------------------------------------------- vocabulary


@pytest.mark.parametrize(
    ("literal", "vocabulary"),
    [
        (schemas.FindingScope, detection.FINDING_SCOPES),
        (schemas.ClosedReason, detection.CLOSED_REASONS),
        (schemas.SignalName, detection.SIGNALS),
        (schemas.FindingSeverity, SEVERITIES),
    ],
)
def test_the_wire_vocabulary_is_the_stored_one(literal: Any, vocabulary: tuple[str, ...]) -> None:
    assert sorted(get_args(literal)) == sorted(vocabulary)


def test_every_signal_has_a_unit() -> None:
    assert sorted(SIGNAL_UNITS) == sorted(detection.SIGNALS)


def test_the_view_states_are_the_stored_ones_and_acknowledged() -> None:
    assert sorted(get_args(schemas.FindingState)) == sorted(
        (*detection.FINDING_STATES, "acknowledged")
    )


# ------------------------------------------------------------------ state


def test_an_open_finding_nobody_acknowledged_is_open() -> None:
    assert view_state("open", None) == "open"


def test_an_open_finding_somebody_acknowledged_is_acknowledged() -> None:
    assert view_state("open", STARTED) == "acknowledged"


def test_a_closed_finding_is_closed_whether_or_not_it_was_acknowledged() -> None:
    assert (view_state("closed", None), view_state("closed", STARTED)) == ("closed", "closed")


# ---------------------------------------------------------------- signals


def test_signals_keep_their_stored_order_and_gain_their_unit() -> None:
    parsed = parse_signals([FAILING, SLOWER])

    assert [(signal.signal, signal.unit) for signal in parsed] == [
        ("failing", "%"),
        ("slower", "ms"),
    ]
    assert parsed[0].endpoint == "POST /checkout"
    assert parsed[0].worst is None


def test_the_column_default_is_a_finding_with_no_signals_yet() -> None:
    assert parse_signals({}) == []


def test_a_signal_the_vocabulary_does_not_know_is_refused_not_shown() -> None:
    with pytest.raises(ValidationError):
        parse_signals([SLOWER | {"signal": "louder"}])


# ------------------------------------------------------------------ trend


def test_a_slower_trend_is_the_window_p95() -> None:
    readings = [_reading(window_p95_ms=200.0), _reading(window_p95_ms=410.0)]
    assert trend_values("slower", readings) == [200.0, 410.0]


def test_a_failing_trend_is_the_window_s_failure_percentage() -> None:
    assert trend_values("failing", [_reading(window_requests=400, window_errors=10)]) == [2.5]


def test_a_traffic_trend_is_requests_per_minute() -> None:
    assert trend_values("traffic", [_reading(window_requests=600, window_minutes=5)]) == [120.0]


@pytest.mark.parametrize(
    ("signal", "reading"),
    [
        ("slower", _reading(window_p95_ms=None)),
        ("failing", _reading(window_requests=0, window_errors=0)),
        ("traffic", _reading(window_minutes=0)),
    ],
)
def test_a_reading_with_no_value_is_left_out_not_drawn_as_zero(
    signal: schemas.SignalName, reading: dict[str, Any]
) -> None:
    assert trend_values(signal, [reading, _reading()]) == trend_values(signal, [_reading()])


# ------------------------------------------------------------------ stats


def test_the_false_finding_rate_is_per_service_week() -> None:
    # 3 false alarms over 4 services for 14 days: 3 / 8 service-weeks.
    assert false_finding_rate(false_alarms=3, services=4, days=14) == pytest.approx(0.375)


def test_with_no_judged_service_there_is_no_rate_rather_than_zero() -> None:
    assert false_finding_rate(false_alarms=0, services=0, days=7) is None


# ---------------------------------------------------------------- mapping


def test_a_row_maps_to_the_contract_the_web_client_reads() -> None:
    row = FindingRow(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        scope="service",
        service_name="checkout",
        severity="critical",
        state="open",
        closed_reason=None,
        muted=False,
        acknowledged_by=uuid.uuid4(),
        acknowledged_at=STARTED,
        feedback_reason="a deploy",
        title="checkout is 3x slower than normal since 14:02",
        signals=[SLOWER],
        started_at=STARTED,
        closed_at=None,
    )

    wire = to_finding(row, trend=[1.0, 2.0]).model_dump(mode="json")

    assert set(wire) == {
        "id",
        "project_id",
        "scope",
        "service_name",
        "title",
        "severity",
        "state",
        "closed_reason",
        "muted",
        "started_at",
        "closed_at",
        "acknowledged_at",
        "signals",
        "trend",
    }
    assert wire["state"] == "acknowledged"
    assert wire["signals"] == [SLOWER | {"unit": "ms"}]
    assert wire["trend"] == [1.0, 2.0]
