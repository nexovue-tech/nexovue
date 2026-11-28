"""The chat bed's questions and their answers, generated from the seed (F4 plan T4.1; AC23,
AC51, AC76).

Each `Question` names the tool whose read holds its answer (`tool`, with the arguments a
careful reader would give), the tools whose steps may carry it (`tools`), the answer
(`expected`, computed from `seed.py`, never typed), and how to take that answer from the
tool's rows (`pick`). `test_chat_seed_live.py` reads every named tool through the real
API with a question's ticket and checks `pick` on what came back equals `expected`.

**Latency questions ask for names, not figures:** a quantile state merged by ClickHouse is
an estimate, so a figure would test the estimator; which endpoint got slow does not.

`answers.json` is this module's output at a fixed anchor (`CANONICAL_ANCHOR`), committed so
a reader can see every question and answer; `test_chat_seed.py` holds it current. The bed
computes each trial's answers again at that trial's own anchor.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from chat import seed
from chat.seed import (
    CART,
    CHARGE,
    CHECKOUT,
    CUSTOM_METRIC,
    EXCEPTION,
    FULL_HOST,
    INVENTORY,
    NEW_VERSION,
    OTHER_SERVICE,
    PAYMENT,
    PERSON_ZONE,
    PROJECT_ZONE,
    ROOMY_HOST,
    SEARCH,
    SLOW_ENDPOINT,
    THIRD_PARTY,
)

ANSWER_FILE: Final = Path(__file__).with_name("answers.json")
CANONICAL_ANCHOR: Final = datetime(2026, 11, 22, 10, 37, tzinfo=UTC)
MINIMUM_QUESTIONS: Final = 50

Kind = Literal["number", "name", "word", "abstain", "tenant"]
Unit = Literal["count", "fraction"]
# Tools whose rows are counted before sampling: a "how many" answer must cite one (AC23).
ROLLUP_TOOLS: Final = (
    "list_services",
    "get_service",
    "list_service_endpoints",
    "get_service_trend",
    "get_service_map",
)
STOPPED_WORDS: Final = ("stopped", "no traffic", "no requests", "not reporting", "not sending")
LEARNING_WORDS: Final = ("learning",)


@dataclass(frozen=True, slots=True)
class Pick:
    """How an answer is read from a tool's rows: `field` of the row matching `where`;
    `top`, the `name` of the row with the largest `field`; `none`, a word when no row
    matches `where`; `first`, `field` of row 0."""

    how: Literal["field", "top", "none", "first"]
    field: str = ""
    where: Mapping[str, str] = dataclass_field(default_factory=dict)
    name: str = ""
    word: str = ""


def picked(pick: Pick, rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The answer `pick` reads from `rows`, as text, or None when the rows do not hold it."""
    matching = [
        row for row in rows if all(str(row.get(key)) == value for key, value in pick.where.items())
    ]
    if pick.how == "none":
        return pick.word if not matching else None
    if pick.how == "first":
        return None if not rows else _text(rows[0].get(pick.field))
    if pick.how == "top":
        ranked = [row for row in matching if isinstance(row.get(pick.field), int | float)]
        if not ranked:
            return None
        return _text(max(ranked, key=lambda row: row[pick.field]).get(pick.name))
    if len(matching) != 1:
        return None
    return _text(matching[0].get(pick.field))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        return figure(value)
    return str(value)


def figure(value: float) -> str:
    """A number as an answer states it: whole numbers bare, fractions to two places."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0")


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str
    kind: Kind
    category: str
    expected: tuple[str, ...] = ()
    unit: Unit | None = None
    # The tool whose read holds the answer, with a careful reader's arguments.
    tool: str | None = None
    arguments: Mapping[str, str] = dataclass_field(default_factory=dict)
    # Tools whose steps may carry the answer.
    tools: tuple[str, ...] = ()
    pick: Pick | None = None
    # The range a right answer reads, as ISO strings in UTC, or None.
    window_from: str | None = None
    window_to: str | None = None
    # The person's zone, sent as the question's context; None is the project's.
    zone: str | None = None
    project: Literal["main", "empty"] = "main"

    @property
    def window(self) -> tuple[datetime, datetime] | None:
        if self.window_from is None or self.window_to is None:
            return None
        return datetime.fromisoformat(self.window_from), datetime.fromisoformat(self.window_to)


def _slug(endpoint: str) -> str:
    return endpoint.split()[-1].strip("/").replace("/", "-")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _last(anchor: datetime, minutes: int) -> tuple[datetime, datetime]:
    return anchor - timedelta(minutes=minutes), anchor


def _yesterday(anchor: datetime, hour: int, zone: str) -> tuple[datetime, datetime]:
    local = ZoneInfo(zone)
    day = anchor.astimezone(local).date() - timedelta(days=1)
    start = datetime.combine(day, time(hour), local).astimezone(UTC)
    return start, start + timedelta(hours=1)


def _whole_yesterday(anchor: datetime) -> tuple[datetime, datetime]:
    local = ZoneInfo(PROJECT_ZONE)
    day = anchor.astimezone(local).date() - timedelta(days=1)
    start = datetime.combine(day, time(0), local).astimezone(UTC)
    return start, start + timedelta(days=1)


def _windowed(
    base: dict[str, Any], window: tuple[datetime, datetime], **arguments: str
) -> dict[str, Any]:
    start, end = window
    return {
        **base,
        "arguments": {"from": _iso(start), "to": _iso(end), **arguments},
        "window_from": _iso(start),
        "window_to": _iso(end),
    }


def _count(
    anchor: datetime,
    qid: str,
    text: str,
    *,
    service: str,
    window: tuple[datetime, datetime],
    field_name: str = "request_count",
    endpoint: str | None = None,
    zone: str | None = None,
    category: str = "how_many",
) -> Question:
    requests, errors = seed.sums(
        anchor, service=service, start=window[0], end=window[1], span_name=endpoint
    )
    value = requests if field_name == "request_count" else errors
    tool = "list_service_endpoints" if endpoint else "get_service"
    where = {"service_name": service, **({"endpoint": endpoint} if endpoint else {})}
    return Question(
        **_windowed(
            {
                "id": qid,
                "text": text,
                "kind": "number",
                "category": category,
                "expected": (str(value),),
                "unit": "count",
                "tool": tool,
                "tools": ROLLUP_TOOLS,
                "pick": Pick("field", field=field_name, where=where),
                "zone": zone,
            },
            window,
            service=service,
        )
    )


def questions(anchor: datetime) -> tuple[Question, ...]:
    """Every question, its answer computed from the seed at `anchor`."""
    hour, half, three = _last(anchor, 60), _last(anchor, 30), _last(anchor, 180)
    made: list[Question] = []
    for service in (CHECKOUT, PAYMENT, SEARCH):
        made += [
            _count(
                anchor,
                f"requests-hour-{service}",
                f"How many requests did {service} serve in the last hour?",
                service=service,
                window=hour,
            ),
            _count(
                anchor,
                f"errors-hour-{service}",
                f"How many requests to {service} failed in the last hour?",
                service=service,
                window=hour,
                field_name="error_count",
            ),
            _count(
                anchor,
                f"requests-3h-{service}",
                f"How many requests did {service} serve in the last 3 hours?",
                service=service,
                window=three,
            ),
            _count(
                anchor,
                f"requests-30m-{service}",
                f"How many requests did {service} handle in the last 30 minutes?",
                service=service,
                window=half,
            ),
        ]
    for endpoint in (SLOW_ENDPOINT, CART):
        made += [
            _count(
                anchor,
                f"endpoint-requests-{_slug(endpoint)}",
                f"How many requests did checkout's {endpoint} endpoint get in the last hour?",
                service=CHECKOUT,
                endpoint=endpoint,
                window=hour,
            ),
            _count(
                anchor,
                f"endpoint-errors-{_slug(endpoint)}",
                f"How many of checkout's {endpoint} requests failed in the last hour?",
                service=CHECKOUT,
                endpoint=endpoint,
                window=hour,
                field_name="error_count",
            ),
        ]
    made.append(
        _count(
            anchor,
            "endpoint-requests-charge",
            "How many requests did payment's POST /charge endpoint get in the last 3 hours?",
            service=PAYMENT,
            endpoint=CHARGE,
            window=three,
        )
    )
    yesterday = _whole_yesterday(anchor)
    for service in (CHECKOUT, PAYMENT):
        made.append(
            _count(
                anchor,
                f"errors-yesterday-{service}",
                f"How many requests to {service} failed yesterday?",
                service=service,
                window=yesterday,
                field_name="error_count",
            )
        )
    # AC76: the person is 5.5 hours ahead of the project; "yesterday at 14:00" is theirs.
    for clock_hour in (9, 14, 22):
        made.append(
            _count(
                anchor,
                f"zone-{clock_hour}",
                f"How many requests did checkout serve yesterday between {clock_hour}:00 and "
                f"{clock_hour + 1}:00?",
                service=CHECKOUT,
                window=_yesterday(anchor, clock_hour, PERSON_ZONE),
                zone=PERSON_ZONE,
                category="zones",
            )
        )
    made.append(
        _count(
            anchor,
            "zone-same-14",
            "How many requests did checkout serve yesterday between 14:00 and 15:00?",
            service=CHECKOUT,
            window=_yesterday(anchor, 14, PROJECT_ZONE),
            zone=PROJECT_ZONE,
            category="zones",
        )
    )
    # AC23's trap: the word "traces" invites counting a sampled list.
    made.append(
        _count(
            anchor,
            "traces-trap",
            "How many checkout traces failed in the last hour?",
            service=CHECKOUT,
            window=hour,
            field_name="error_count",
            category="how_many_sampled",
        )
    )
    made += _names(anchor, hour, half)
    made += _calls(anchor, hour)
    made += _hosts_and_metrics(anchor, hour)
    made += _abstentions(anchor, hour)
    return tuple(made)


def _name(
    qid: str,
    text: str,
    *,
    expected: str,
    tool: str,
    pick: Pick,
    window: tuple[datetime, datetime] | None,
    category: str,
    kind: Kind = "name",
    accepted: tuple[str, ...] | None = None,
    **arguments: str,
) -> Question:
    base: dict[str, Any] = {
        "id": qid,
        "text": text,
        "kind": kind,
        "category": category,
        "expected": accepted or (expected,),
        "tool": tool,
        "tools": (tool,),
        "pick": pick,
    }
    if window is None:
        return Question(**base, arguments=arguments)
    return Question(**_windowed(base, window, **arguments))


def _names(
    anchor: datetime, hour: tuple[datetime, datetime], half: tuple[datetime, datetime]
) -> list[Question]:
    del anchor
    return [
        _name(
            "slow-endpoint",
            "Which endpoint of checkout got slow in the last 30 minutes?",
            expected=SLOW_ENDPOINT,
            tool="list_service_endpoints",
            pick=Pick("top", field="p95_ms", name="endpoint", where={"service_name": CHECKOUT}),
            window=half,
            category="slow_endpoint",
            service=CHECKOUT,
        ),
        _name(
            "most-errors",
            "Which service had the most failed requests in the last hour?",
            expected=PAYMENT,
            tool="list_services",
            pick=Pick("top", field="error_count", name="service_name"),
            window=hour,
            category="names",
        ),
        _name(
            "slowest-service",
            "Which service is slowest right now, judged on the last 30 minutes?",
            expected=CHECKOUT,
            tool="list_services",
            pick=Pick("top", field="p95_ms", name="service_name"),
            window=half,
            category="names",
        ),
        _name(
            "busiest-service",
            "Which service served the most requests in the last hour?",
            expected=CHECKOUT,
            tool="list_services",
            pick=Pick("top", field="request_count", name="service_name"),
            window=hour,
            category="names",
        ),
        _name(
            "stopped",
            "Is inventory still sending traffic?",
            expected="no traffic",
            accepted=STOPPED_WORDS,
            kind="word",
            tool="list_services",
            pick=Pick("none", where={"service_name": INVENTORY}, word="no traffic"),
            window=hour,
            category="stopped_reporting",
        ),
        _name(
            "learning",
            "Is nexovue's detection judging search yet?",
            expected="learning",
            accepted=LEARNING_WORDS,
            kind="word",
            tool="get_service_detection",
            pick=Pick("first", field="state"),
            window=hour,
            category="still_learning",
            service=SEARCH,
        ),
        _name(
            "error-type",
            "Which exception does payment raise most in the last hour?",
            expected=EXCEPTION,
            tool="list_error_groups",
            pick=Pick("top", field="occurrences", name="exception_type"),
            window=hour,
            category="errors",
            service=PAYMENT,
        ),
        _name(
            "open-finding",
            "Which service has an open finding?",
            expected=PAYMENT,
            tool="list_findings",
            pick=Pick("field", field="service_name", where={"state": "open"}),
            window=None,
            category="findings",
            state="open",
        ),
        _name(
            "latest-release",
            "Which version of checkout was released most recently?",
            expected=NEW_VERSION,
            tool="list_releases",
            pick=Pick("first", field="version"),
            window=_last(hour[1], 6 * 60),
            category="releases",
            service=CHECKOUT,
        ),
    ]


def _calls(anchor: datetime, hour: tuple[datetime, datetime]) -> list[Question]:
    calls, errors = seed.edge_sums(
        anchor, client=PAYMENT, server=THIRD_PARTY, start=hour[0], end=hour[1]
    )
    checkout_calls, _ = seed.edge_sums(
        anchor, client=CHECKOUT, server=PAYMENT, start=hour[0], end=hour[1]
    )
    edges: dict[str, Any] = {"tool": "get_service_map", "tools": ("get_service_map",)}
    return [
        _name(
            "third-party-host",
            "Which outside host does payment call?",
            expected=THIRD_PARTY,
            tool="get_service_map",
            pick=Pick("field", field="callee", where={"caller": PAYMENT}),
            window=hour,
            category="third_party_host",
        ),
        Question(
            **_windowed(
                {
                    **edges,
                    "id": "third-party-errors",
                    "text": (
                        f"How many of payment's calls to {THIRD_PARTY} failed in the last hour?"
                    ),
                    "kind": "number",
                    "category": "third_party_host",
                    "expected": (str(errors),),
                    "unit": "count",
                    "pick": Pick(
                        "field",
                        field="error_count",
                        where={"caller": PAYMENT, "callee": THIRD_PARTY},
                    ),
                },
                hour,
            )
        ),
        Question(
            **_windowed(
                {
                    **edges,
                    "id": "third-party-calls",
                    "text": f"How many calls did payment make to {THIRD_PARTY} in the last hour?",
                    "kind": "number",
                    "category": "third_party_host",
                    "expected": (str(calls),),
                    "unit": "count",
                    "pick": Pick(
                        "field",
                        field="call_count",
                        where={"caller": PAYMENT, "callee": THIRD_PARTY},
                    ),
                },
                hour,
            )
        ),
        Question(
            **_windowed(
                {
                    **edges,
                    "id": "checkout-calls-payment",
                    "text": "How many calls did checkout make to payment in the last hour?",
                    "kind": "number",
                    "category": "how_many",
                    "expected": (str(checkout_calls),),
                    "unit": "count",
                    "pick": Pick(
                        "field", field="call_count", where={"caller": CHECKOUT, "callee": PAYMENT}
                    ),
                },
                hour,
            )
        ),
        _name(
            "checkout-callee",
            "Which service does checkout call?",
            expected=PAYMENT,
            tool="get_service_map",
            pick=Pick("field", field="callee", where={"caller": CHECKOUT}),
            window=hour,
            category="names",
        ),
        Question(
            **_windowed(
                {
                    "id": "payment-error-rate",
                    "text": "What share of payment's requests failed in the last hour?",
                    "kind": "number",
                    "category": "rates",
                    "expected": (figure(0.3),),
                    "unit": "fraction",
                    "tool": "get_service",
                    "tools": ROLLUP_TOOLS,
                    "pick": Pick("field", field="error_rate", where={"service_name": PAYMENT}),
                },
                hour,
                service=PAYMENT,
            )
        ),
    ]


def _hosts_and_metrics(anchor: datetime, hour: tuple[datetime, datetime]) -> list[Question]:
    del anchor
    hosts: dict[str, Any] = {"tool": "list_hosts", "tools": ("list_hosts",), "unit": "fraction"}
    return [
        Question(
            **_windowed(
                {
                    **hosts,
                    "id": f"disk-{host}",
                    "text": f"Is the disk full on {host}? How full is it?",
                    "kind": "number",
                    "category": "disk",
                    "expected": (figure(seed.filesystem_utilization(host)),),
                    "pick": Pick("field", field="filesystem_utilization", where={"host": host}),
                },
                hour,
            )
        )
        for host in (FULL_HOST, ROOMY_HOST)
    ] + [
        _name(
            "fullest-disk",
            "Which host's disk is fullest?",
            expected=FULL_HOST,
            tool="list_hosts",
            pick=Pick("top", field="filesystem_utilization", name="host"),
            window=hour,
            category="disk",
        ),
        _name(
            "custom-metric",
            "Which metric reports the order queue's depth?",
            expected=CUSTOM_METRIC,
            tool="list_metrics",
            pick=Pick("field", field="metric_name", where={"metric_name": CUSTOM_METRIC}),
            window=hour,
            category="custom_metric",
        ),
    ]


def _abstentions(anchor: datetime, hour: tuple[datetime, datetime]) -> list[Question]:
    del anchor
    unanswerable = (
        ("who-complained", "Which customer reported the checkout problem first?"),
        (
            "pool-exhausted",
            "Did the Postgres connection pool run out of connections in the last hour?",
        ),
        ("next-monday", "What will checkout's traffic be next Monday?"),
        ("status-page", "Is the card network's own status page reporting an outage?"),
        ("long-ago", "How many requests did checkout serve on 1 January 2020?"),
        ("why-slow", "Who changed the checkout code that made it slow?"),
    )
    made = [
        Question(id=qid, text=text, kind="abstain", category="not_enough_evidence")
        for qid, text in unanswerable
    ]
    made.append(
        Question(
            id="empty-project",
            text="How many requests did this project serve in the last hour?",
            kind="abstain",
            category="no_telemetry",
            project="empty",
        )
    )
    # Another organization's service: its rows must never reach the model or the answer.
    for qid, text in (
        ("other-tenant-count", f"How many requests did {OTHER_SERVICE} serve in the last hour?"),
        ("other-tenant-errors", f"Did {OTHER_SERVICE} have any failures in the last hour?"),
    ):
        made.append(
            Question(
                **_windowed(
                    {
                        "id": qid,
                        "text": text,
                        "kind": "tenant",
                        "category": "across_tenants",
                        "tool": "get_service",
                        "tools": ROLLUP_TOOLS,
                        "pick": Pick("none", where={"service_name": OTHER_SERVICE}, word="none"),
                        "expected": ("none",),
                    },
                    hour,
                    service=OTHER_SERVICE,
                )
            )
        )
    return made


def answer_file(anchor: datetime = CANONICAL_ANCHOR) -> str:
    """The questions and answers as committed JSON."""
    rows = []
    for question in questions(anchor):
        row = asdict(question)
        row["arguments"] = dict(question.arguments)
        if question.pick is not None:
            row["pick"] = {**asdict(question.pick), "where": dict(question.pick.where)}
        rows.append(row)
    return json.dumps({"anchor": anchor.isoformat(), "questions": rows}, indent=2) + "\n"
