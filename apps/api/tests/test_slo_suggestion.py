"""`GET /slo-readings/suggestion` (F19 plan T2.4): FR7's and FR8's arithmetic on four
weeks read from ClickHouse, with no model (D10).

AC75 (a bad week suggests from the typical week and says so; worst and typical below
90% suggest nothing and say why; under 7 days offers the labelled starter), AC101's
latency suggestion (the boundary holding 95% of requests in every week) and check
wording (what the interval allows), FR34's pre-ticked health checks, too few requests
offering a check, and who may read it: any person, a viewer included, and never a token.

The repository is the real one over a fake client, so each statement is still refused
without the tenant binding, and a statement asking for another project gets nothing.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
from nexovue_platform import slo

from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.ratelimit.policy import TELEMETRY_TIER, tier_for_path
from nexovue_api.settings import API_PREFIX
from nexovue_api.slos.readings import queries, service
from nexovue_api.slos.readings.schemas import CheckFacts

TENANT: Final = str(uuid.uuid4())
PROJECT: Final = uuid.uuid4()
NOW: Final = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
WEEK: Final = timedelta(days=7)
MILLION: Final = 1_000_000


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.column_names = list(rows[0]) if rows else []
        self.result_rows = [tuple(row.values()) for row in rows]


class _Client:
    """Answers each of the module's statements with the rows a test set, only for the
    tenant and project it was given."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self._rows = rows
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def query(
        self, sql: str, *, parameters: dict[str, Any], settings: dict[str, Any]
    ) -> _Result:
        self.asked.append((sql, parameters))
        known = {getattr(queries, name): name for name in dir(queries) if name.endswith("_SQL")}
        assert sql in known, f"not one of the suggestion's statements:\n{sql}"
        if parameters["tenant_id"] != TENANT or parameters["project_id"] != str(PROJECT):
            return _Result([])
        return _Result(self._rows.get(known[sql], []))


def _repository(rows: dict[str, list[dict[str, Any]]]) -> tuple[TenantScopedRepository, _Client]:
    client = _Client(rows)
    return TenantScopedRepository(client, tenant_id=TENANT), client  # type: ignore[arg-type]


def _weeks(*weeks: tuple[int, int], first: datetime = NOW - 4 * WEEK) -> list[dict[str, Any]]:
    """Rows of (week, total, errors), week 0 the newest."""
    return [
        {"week": index, "total": total, "errors": errors, "first_minute": first}
        for index, (total, errors) in enumerate(weeks)
    ]


async def _suggest(
    rows: dict[str, list[dict[str, Any]]],
    kind: str = slo.SERVICE_AVAILABILITY,
    *,
    check: CheckFacts | None = None,
    has_checks: bool = False,
    endpoint: str | None = None,
) -> Any:
    repository, _ = _repository(rows)
    return await service.suggest(
        repository,
        project_id=PROJECT,
        kind=kind,
        service_name=None if kind == slo.CHECK_AVAILABILITY else "checkout",
        endpoint_name=endpoint,
        check=check,
        has_checks=has_checks,
        now=NOW,
    )


# ------------------------------------------------------------------------ AC75


async def test_ac75_a_bad_week_suggests_from_the_typical_week_and_warns() -> None:
    """99.95% for three weeks and 98.7% for one: the worst rounds to 98, four steps under
    the typical week's 99.95, so the typical week is suggested, with the warning."""
    rows = {
        "SERVICE_WEEKS_SQL": _weeks(
            (MILLION, 500), (MILLION, 13_000), (MILLION, 500), (MILLION, 500)
        )
    }
    suggestion = await _suggest(rows)
    assert suggestion.outcome == slo.Suggested.FROM_TYPICAL_WEEK
    assert suggestion.objective == Decimal("99.95")
    assert (suggestion.worst, suggestion.typical, suggestion.best) == (
        Decimal("98.70"),
        Decimal("99.95"),
        Decimal("99.95"),
    )
    assert [week.figure for week in suggestion.weeks] == [
        Decimal("99.95"),
        Decimal("98.70"),
        Decimal("99.95"),
        Decimal("99.95"),
    ]


async def test_fr7_steady_weeks_suggest_the_worst_rounded_down() -> None:
    rows = {
        "SERVICE_WEEKS_SQL": _weeks((MILLION, 700), (MILLION, 800), (MILLION, 600), (MILLION, 900))
    }
    suggestion = await _suggest(rows)
    assert suggestion.outcome == slo.Suggested.FROM_WORST_WEEK
    # The worst week is 99.91%: rounded down to 99.9, never up.
    assert suggestion.worst == Decimal("99.91")
    assert suggestion.objective == Decimal("99.9")


async def test_ac75_worst_and_typical_below_90_suggest_nothing() -> None:
    rows = {"SERVICE_WEEKS_SQL": _weeks(*[(1_000, 200)] * 4)}
    suggestion = await _suggest(rows)
    assert suggestion.outcome == slo.Suggested.BELOW_LOWEST
    assert suggestion.objective is None
    assert suggestion.worst == Decimal("80.00")


async def test_ac75_under_7_days_offers_the_labelled_starter() -> None:
    rows = {"SERVICE_WEEKS_SQL": _weeks((50_000, 3), first=NOW - timedelta(days=3))}
    suggestion = await _suggest(rows)
    assert suggestion.outcome == slo.Suggested.STARTER
    assert suggestion.objective == slo.STARTER_OBJECTIVE == Decimal("99.5")
    assert suggestion.days_of_data == 3


async def test_fr8_too_few_requests_suggest_nothing_and_offer_a_check() -> None:
    """2,000 requests over four weeks: 99.9% would allow 2 failures, too few to measure
    (FR12), so there is no suggestion, and the project's check is offered."""
    rows = {"SERVICE_WEEKS_SQL": _weeks(*[(500, 0)] * 4)}
    suggestion = await _suggest(rows, has_checks=True)
    assert suggestion.outcome == slo.Suggested.TOO_FEW
    assert suggestion.objective is None
    assert suggestion.offer_check is True


async def test_no_history_at_all_is_a_starter_not_a_failure() -> None:
    suggestion = await _suggest({})
    assert suggestion.outcome == slo.Suggested.STARTER
    assert suggestion.days_of_data == 0


# --------------------------------------------------------------- AC101 latency


def _buckets_holding(index: int) -> list[int]:
    """1,000 requests a week with exactly 95% at or under boundary `index`."""
    buckets = [0] * slo.BUCKET_COUNT
    buckets[index] = 950
    buckets[index + 3] = 50
    return buckets


async def test_ac101_the_latency_suggestion_is_the_boundary_holding_95_percent() -> None:
    """Every week must hold 95% under the boundary, so the slowest week decides it."""
    fast, slow = slo.boundary_at_or_under(200.0), slo.boundary_at_or_under(500.0)
    assert fast is not None and slow is not None
    weeks = [_buckets_holding(fast), _buckets_holding(slow), _buckets_holding(fast)]
    rows = {
        "LATENCY_WEEKS_SQL": [
            {"week": index, "buckets": buckets, "first_minute": NOW - 4 * WEEK}
            for index, buckets in enumerate(weeks)
        ]
    }
    suggestion = await _suggest(rows, slo.SERVICE_LATENCY)
    assert suggestion.outcome == slo.Suggested.FROM_WORST_WEEK
    assert suggestion.boundary_index == slow
    assert round(suggestion.boundary_ms, 1) == 487.2
    assert suggestion.objective == Decimal("95")
    assert suggestion.highest_boundary_ms == pytest.approx(slo.HIGHEST_BOUNDARY_MS)


# ------------------------------------------------------------ AC101 check, FR8


async def test_ac101_a_check_says_what_its_interval_allows() -> None:
    """Every 5 minutes for 28 days is 8,064 runs: 99.9% allows 8 failed runs; every
    minute would allow 40 (FR8's example)."""
    rows = {
        "CHECK_WEEKS_SQL": [
            {"week": index, "total": 2_016, "good": 2_016, "first_run": NOW - 4 * WEEK}
            for index in range(4)
        ]
    }
    facts = CheckFacts(id=uuid.uuid4(), interval_seconds=300, locations=1)
    suggestion = await _suggest(rows, slo.CHECK_AVAILABILITY, check=facts, has_checks=True)
    assert suggestion.check is not None
    runs = {window.window_days: window for window in suggestion.check.windows}
    assert runs[28].expected_runs == 8_064
    assert runs[28].fastest_expected_runs == 40_320
    assert slo.allowed_failures(Decimal("99.9"), runs[28].expected_runs) == 8
    assert suggestion.check.alerts_can_judge is True
    # A check target never offers a check target (AC11).
    assert suggestion.offer_check is False
    assert suggestion.steps == list(slo.SUGGESTION_STEPS)


async def test_fr16_a_check_every_6_hours_cannot_be_judged() -> None:
    facts = CheckFacts(id=uuid.uuid4(), interval_seconds=21_600, locations=1)
    suggestion = await _suggest({}, slo.CHECK_AVAILABILITY, check=facts)
    assert suggestion.check is not None
    assert suggestion.check.alerts_can_judge is False


# ------------------------------------------------------------------ FR34, AC80


async def test_fr34_a_service_target_lists_its_endpoints_with_health_checks_ticked() -> None:
    rows = {
        "SERVICE_WEEKS_SQL": _weeks(*[(MILLION, 500)] * 4),
        "ENDPOINT_NAMES_SQL": [
            {"span_name": "POST /checkout", "requests": 900_000},
            {"span_name": "GET /health", "requests": 80_000},
            {"span_name": "GET /readyz", "requests": 20_000},
        ],
    }
    suggestion = await _suggest(rows)
    assert [(e.name, e.health_check) for e in suggestion.endpoints] == [
        ("POST /checkout", False),
        ("GET /health", True),
        ("GET /readyz", True),
    ]


async def test_an_endpoint_target_reads_that_endpoint_s_weeks() -> None:
    rows = {"ENDPOINT_WEEKS_SQL": _weeks(*[(MILLION, 500)] * 4)}
    suggestion = await _suggest(rows, slo.ENDPOINT_AVAILABILITY, endpoint="POST /checkout")
    assert suggestion.objective == Decimal("99.95")


async def test_every_statement_binds_the_project_and_another_project_reads_nothing() -> None:
    repository, client = _repository({"SERVICE_WEEKS_SQL": _weeks(*[(MILLION, 500)] * 4)})
    elsewhere = await service.suggest(
        repository,
        project_id=uuid.uuid4(),
        kind=slo.SERVICE_AVAILABILITY,
        service_name="checkout",
        endpoint_name=None,
        check=None,
        has_checks=False,
        now=NOW,
    )
    assert elsewhere.outcome == slo.Suggested.STARTER
    assert client.asked and all("project_id" in parameters for _, parameters in client.asked)
    for name in dir(queries):
        if name.endswith("_SQL"):
            assert "{project_id:String}" in getattr(queries, name), name


# -------------------------------------------------------------- who may read it

ROUTER: Final = Path(service.__file__).resolve().parent / "router.py"


def _suggestion_handler_annotations() -> str:
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    [handler] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "read_suggestion"
    ]
    return " ".join(
        ast.unparse(argument.annotation)
        for argument in handler.args.args
        if argument.annotation is not None
    )


def test_ac32_any_person_reads_it_a_viewer_included() -> None:
    """A person's identity and no action: every role reads it (AC32)."""
    annotations = _suggestion_handler_annotations()
    assert "CurrentIdentity" in annotations
    assert "May" not in annotations


def test_ac33_a_token_is_refused_because_it_takes_a_person() -> None:
    """`CurrentIdentity` refuses an API token with 403, below every route
    (`auth/dependencies.py`, `_refuse_a_token_here`); `CurrentReader` would admit one."""
    assert "CurrentReader" not in _suggestion_handler_annotations()


def test_it_is_on_the_telemetry_tier() -> None:
    assert tier_for_path(f"{API_PREFIX}/slo-readings/suggestion") is TELEMETRY_TIER
    # The list must stay off it, to answer while ClickHouse is down (AC40, D59).
    assert tier_for_path(f"{API_PREFIX}/slos") is not TELEMETRY_TIER
