"""What F28's readers hand F3, the notices and the stored reading (plan T5.2, T5.5).

AC45: the facts are an exact allow list, held field by field, and a field added to any
of them fails here until it is judged. The sender's `metadata`, `url`, `source` and
`delivery_id` are free text and appear in no fact and in no statement that builds one,
with the hostile fixture F3's injection suite uses (`hostile_release_names.py`).
`release_comparison` runs the API's own rule over what the store reads.

No store: the statements are read as text, and the comparison runs over a recording
connection and store. `test_release_facts_live.py` runs the readers against Postgres.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Final

from hostile_release_names import HOSTILE_VERSIONS
from nexovue_platform import releases
from nexovue_platform.releases import DeployFact
from nexovue_platform.slo import BUCKET_COUNT
from sqlalchemy.sql.elements import TextClause

from nexovue_scheduler.releases import facts
from nexovue_scheduler.releases.facts import ComparisonFact, PeriodFact

ORG_ID: Final = uuid.uuid4()
PROJECT_ID: Final = uuid.uuid4()
PERIOD_ID: Final = uuid.uuid4()
RELEASE_ID: Final = uuid.uuid4()
START: Final = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
MINUTE: Final = timedelta(minutes=1)

# AC45's lists. A new field is a decision: add it here only once it is judged a fact, a
# name F3 aliases, or a number; never the sender's free text.
DEPLOY_FACT_FIELDS: Final = {
    "deploy_id",
    "release_id",
    "service_name",
    "version",
    "deployed_at",
    "received_at",
    "state",
    "commit_sha",
    "basis",
}
PERIOD_FACT_FIELDS: Final = {
    "release_id",
    "period_id",
    "service_name",
    "version",
    "basis",
    "started_at",
    "already_running",
}
COMPARISON_FACT_FIELDS: Final = {
    "release_id",
    "period_id",
    "service_name",
    "version",
    "previous_version",
    "reason",
    "lead",
    "blocks",
    "traffic",
    "compared_to",
    "as_of",
    "minutes_differing",
    "versions_combined",
    "mixed_environments",
}
BLOCK_FIELDS: Final = {
    "kind",
    "method",
    "start",
    "end",
    "minutes_compared",
    "this_version",
    "previous_version",
    "failure_share",
    "p95",
    "previous_start",
    "previous_end",
}
SIDE_FIELDS: Final = {"version", "requests", "errors", "p95_ms", "requests_per_minute"}
READING_FIELDS: Final = {"reading", "note"}
FREE_TEXT: Final = ("metadata", "url", "source", "delivery_id")


def _fields(kind: type) -> set[str]:
    return {field.name for field in dataclasses.fields(kind)}


def test_every_fact_is_exactly_its_allow_list() -> None:
    """AC45: an addition to any fact fails here."""
    assert _fields(DeployFact) == DEPLOY_FACT_FIELDS
    assert _fields(PeriodFact) == PERIOD_FACT_FIELDS
    assert _fields(ComparisonFact) == COMPARISON_FACT_FIELDS
    assert _fields(releases.Block) == BLOCK_FIELDS
    assert _fields(releases.Side) == SIDE_FIELDS
    assert _fields(releases.Reading) == READING_FIELDS


def test_no_fact_carries_the_senders_free_text() -> None:
    every = (
        _fields(DeployFact)
        | _fields(PeriodFact)
        | _fields(ComparisonFact)
        | _fields(releases.Block)
        | _fields(releases.Side)
    )
    assert every.isdisjoint(FREE_TEXT)
    assert "other_versions" not in _fields(ComparisonFact)


def test_a_deploy_fact_is_always_a_signed_record() -> None:
    """D54: `reported` is F3's `verified`; a deploy fact is never a seen version."""
    fact = DeployFact(
        deploy_id=uuid.uuid4(),
        release_id=None,
        service_name="checkout",
        version="v2",
        deployed_at=START,
        received_at=START,
        state="finished",
        commit_sha=None,
    )
    assert fact.basis == releases.REPORTED


def test_no_statement_a_reader_runs_selects_free_text() -> None:
    """T5.5: the readers leave free text out at the source, not after reading it."""
    statements = [value.text for value in vars(facts).values() if isinstance(value, TextClause)]
    assert len(statements) >= 6
    for statement in statements:
        selected = statement.split("FROM", 1)[0]
        for column in FREE_TEXT:
            assert not re.search(rf"\b{column}\b", selected), (column, statement)


# ------------------------------------------------------------- the comparison


class _Connection:
    """Answers the period's one statement with `row`, and records what was bound."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.bound: list[dict[str, Any]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> Any:
        self.bound.append(parameters)
        return SimpleNamespace(first=lambda: self.row)


class _Reader:
    def __init__(
        self,
        minutes: list[releases.VersionMinute],
        totals: list[releases.ServiceMinute],
        labels: list[str],
    ) -> None:
        self.minutes, self.totals, self.labels = minutes, totals, labels

    def compared_minutes(self, **_: Any) -> list[releases.VersionMinute]:
        return self.minutes

    def compared_totals(self, **_: Any) -> list[releases.ServiceMinute]:
        return self.totals

    def environments(self, **_: Any) -> list[str]:
        return self.labels


class _Store:
    def __init__(self, reader: _Reader) -> None:
        self.reader = reader
        self.bound: list[tuple[object, object]] = []

    def for_project(self, org_id: object, project_id: object) -> _Reader:
        self.bound.append((org_id, project_id))
        return self.reader


def _buckets(requests: int) -> tuple[int, ...]:
    buckets = [0] * BUCKET_COUNT
    buckets[20] = requests
    return tuple(buckets)


def _rolling(version: str, previous: str) -> tuple[list[Any], list[Any]]:
    """An hour of the previous version, then 30 minutes of both, side by side."""
    minutes: list[releases.VersionMinute] = []
    for offset in range(-60, 30):
        at = START + offset * MINUTE
        minutes.append(releases.VersionMinute(at, previous, 100, 1, _buckets(100)))
        if offset >= 0:
            minutes.append(releases.VersionMinute(at, version, 100, 1, _buckets(100)))
    totals: dict[datetime, list[int]] = {}
    for row in minutes:
        totals.setdefault(row.minute, [0, 0])
        totals[row.minute][0] += row.requests
        totals[row.minute][1] += row.errors
    return minutes, [
        releases.ServiceMinute(at, requests, errors, _buckets(requests))
        for at, (requests, errors) in sorted(totals.items())
    ]


def _period(version: str, previous: str | None, *, already_running: bool = False) -> Any:
    return SimpleNamespace(
        period_id=PERIOD_ID,
        release_id=RELEASE_ID,
        service_name="checkout",
        version=version,
        started_at=START,
        start_basis=releases.SEEN,
        previous_version=previous,
        already_running=already_running,
        previous_started_at=START - timedelta(days=1),
        settled_minute=START + 29 * MINUTE,
        kept_days=30,
    )


async def _compare(row: Any, reader: _Reader) -> tuple[ComparisonFact | None, _Store, _Connection]:
    connection, store = _Connection(row), _Store(reader)
    found = await facts.release_comparison(
        store,  # type: ignore[arg-type]
        connection,  # type: ignore[arg-type]
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        period_id=PERIOD_ID,
        window=releases.DEFAULT_WINDOW,
        now=START + timedelta(minutes=40),
    )
    return found, store, connection


async def test_a_rolling_release_compares_side_by_side_by_the_shared_rule() -> None:
    minutes, totals = _rolling("v2", "v1")
    found, store, connection = await _compare(_period("v2", "v1"), _Reader(minutes, totals, []))

    assert found is not None
    assert (found.reason, found.lead) == (None, releases.ROLLOUT)
    (block,) = found.blocks
    assert block.method == releases.SIDE_BY_SIDE
    assert (block.this_version.requests, block.previous_version.requests) == (3000, 3000)
    assert found.compared_to == START + 30 * MINUTE and found.as_of is False
    assert (found.minutes_differing, found.versions_combined, found.mixed_environments) == (
        0,
        False,
        False,
    )
    assert store.bound == [(ORG_ID, PROJECT_ID)]
    assert connection.bound == [
        {"org_id": ORG_ID, "project_id": PROJECT_ID, "period_id": PERIOD_ID}
    ]


async def test_two_environment_labels_and_a_combined_minute_are_said() -> None:
    minutes, totals = _rolling("v2", "v1")
    minutes.append(
        releases.VersionMinute(START + MINUTE, releases.OTHER_VERSION, 5, 0, _buckets(5))
    )
    totals = [
        releases.ServiceMinute(t.minute, t.requests + 5, t.errors, t.buckets)
        if t.minute == START + MINUTE
        else t
        for t in totals
    ]
    found, _, _ = await _compare(
        _period("v2", "v1"), _Reader(minutes, totals, ["production", "staging"])
    )

    assert found is not None
    assert (found.versions_combined, found.mixed_environments) == (True, True)


async def test_a_period_not_this_projects_is_none() -> None:
    found, store, _ = await _compare(None, _Reader([], [], []))
    assert found is None
    assert store.bound == []


async def test_a_hostile_version_is_carried_as_a_name_and_nothing_else() -> None:
    """T5.5: a version is a name F3 aliases; the fact gains no field for it to fill."""
    hostile = HOSTILE_VERSIONS[0]
    minutes, totals = _rolling(hostile, "v1")
    found, _, _ = await _compare(_period(hostile, "v1"), _Reader(minutes, totals, []))

    assert found is not None
    assert found.version == hostile
    assert found.blocks[0].this_version.version == hostile
    assert _fields(type(found)) == COMPARISON_FACT_FIELDS
