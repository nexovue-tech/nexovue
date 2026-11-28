"""The chat bed's seed, as pure data (F4 plan T4.1).

**Every time is an offset from a run anchor**, a whole minute: offset `o` is the minute
`anchor - o minutes`. The same spec gives the rows `stores.py` writes and the answers
`answers.py` computes, so an answer is never typed by hand.

What it holds, across logs, traces, rollups, errors, deploys and two organizations
(`tools/detection-seed` writes only `service_stats_1m`):

- **Organization A, project `main`** (zone UTC): `checkout` (two endpoints, 48 hours, its
  `POST /checkout` slow in the last half hour), `payment` (48 hours, failing in the last
  hour, calling the third-party host `api.stripe.com`), `inventory` (stopped reporting an
  hour ago), `search` (new: an hour of traffic, detection still learning); two hosts, one
  of them with a nearly full disk; a custom metric; error logs; one slow trace; releases,
  findings and detection states.
- **Organization A, project `empty`:** reported once, holds no telemetry.
- **Organization B:** one service of its own, so a leak across tenants shows.

**Quiet minutes.** A question about "the last hour" is answered minutes after the seed is
written, and a model reads its own clock, so a window drifts. No endpoint, edge or log row
sits within `SLACK` before a relative window's edge (`QUIET`): a window drifting by less
than `SLACK` reads exactly the same rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Final, Literal

MINUTE: Final = timedelta(minutes=1)
# Relative windows the questions use, in minutes, and how far a window may drift (a guess:
# a local model's question of 10 calls at 30 seconds each, rounded up).
RELATIVE_WINDOWS: Final = (30, 60, 180)
SLACK: Final = 10
# Two days of history: "yesterday" in any zone lies inside it.
HISTORY: Final = 48 * 60

PROJECT_ZONE: Final = "UTC"
# 5.5 hours ahead of the project's zone (AC76).
PERSON_ZONE: Final = "Asia/Kolkata"

Project = Literal["main", "empty", "other"]
CHECKOUT: Final = "checkout"
PAYMENT: Final = "payment"
INVENTORY: Final = "inventory"
SEARCH: Final = "search"
OTHER_SERVICE: Final = "tenant-b-only-service"
THIRD_PARTY: Final = "api.stripe.com"
SLOW_ENDPOINT: Final = "POST /checkout"
CART: Final = "GET /cart"
CHARGE: Final = "POST /charge"
STOCK: Final = "GET /stock"
FIND: Final = "GET /search"
OTHER_ENDPOINT: Final = "GET /b-only"
EXCEPTION: Final = "CardDeclinedError"
EXCEPTION_MESSAGE: Final = "card declined by issuer (code 51)"
CUSTOM_METRIC: Final = "orders.queue.depth"
CUSTOM_UNIT: Final = "{orders}"
FULL_HOST: Final = "db-1"
ROOMY_HOST: Final = "web-1"
FILESYSTEM_METRIC: Final = "system.filesystem.usage"
# Bytes: db-1 is 97% full, web-1 40%.
DISKS: Final[dict[str, tuple[int, int]]] = {
    FULL_HOST: (97_000_000_000, 3_000_000_000),
    ROOMY_HOST: (40_000_000_000, 60_000_000_000),
}
OLD_VERSION: Final = "2.4.0"
NEW_VERSION: Final = "2.4.1"
TRACE_ID: Final = "c0ffee00" * 4
ROOT_SPAN: Final = "a1" * 8
CHILD_SPAN: Final = "b2" * 8
SLOW_MS: Final = 900.0
NORMAL_MS: Final = 100.0


def quiet(offset: int) -> bool:
    """Whether minute `offset` is held empty so a drifting window reads the same rows."""
    if offset < SLACK:
        return True
    return any(window - SLACK < offset <= window for window in RELATIVE_WINDOWS)


@dataclass(frozen=True, slots=True)
class EndpointMinute:
    project: Project
    service: str
    span_name: str
    minute: datetime
    requests: int
    errors: int
    # A few durations, folded into the latency state on insert.
    durations: tuple[float, ...]
    span_kind: str = "SPAN_KIND_SERVER"


def _hour(anchor: datetime, offset: int) -> int:
    return (anchor - offset * MINUTE).hour


def _payment_failing(offset: int) -> bool:
    return SLACK <= offset <= 60 - SLACK


def endpoint_minutes(anchor: datetime) -> Iterator[EndpointMinute]:
    """Every rollup row, newest first per service."""
    for offset in range(HISTORY):
        if quiet(offset):
            continue
        minute = anchor - offset * MINUTE
        hour = _hour(anchor, offset)
        slow = offset <= 30 - SLACK
        yield EndpointMinute(
            "main",
            CHECKOUT,
            SLOW_ENDPOINT,
            minute,
            20 + hour % 5,
            1 if minute.minute % 10 == 0 else 0,
            (SLOW_MS,) * 3 if slow else (NORMAL_MS - 20, NORMAL_MS, NORMAL_MS + 20),
        )
        yield EndpointMinute("main", CHECKOUT, CART, minute, 30 + hour % 3, 0, (40.0,) * 3)
        yield EndpointMinute(
            "main", PAYMENT, CHARGE, minute, 10, 3 if _payment_failing(offset) else 0, (200.0,) * 3
        )
        if 60 < offset < 240:
            yield EndpointMinute("main", INVENTORY, STOCK, minute, 8, 0, (30.0,) * 3)
        if offset <= 60 - SLACK:
            yield EndpointMinute("main", SEARCH, FIND, minute, 5, 0, (60.0,) * 3)
        if offset < 240:
            yield EndpointMinute("other", OTHER_SERVICE, OTHER_ENDPOINT, minute, 999, 7, (5.0,) * 3)


@dataclass(frozen=True, slots=True)
class EdgeMinute:
    client: str
    server: str
    minute: datetime
    calls: int
    errors: int
    # The callee's technology when it never reported for itself: a third-party host.
    peer_kind: str = ""
    edge_kind: str = "http"


def edge_minutes(anchor: datetime) -> Iterator[EdgeMinute]:
    for offset in range(240):
        if quiet(offset):
            continue
        minute = anchor - offset * MINUTE
        yield EdgeMinute(CHECKOUT, PAYMENT, minute, 10, 0)
        yield EdgeMinute(
            PAYMENT, THIRD_PARTY, minute, 10, 3 if _payment_failing(offset) else 0, "http"
        )


@dataclass(frozen=True, slots=True)
class LogLine:
    service: str
    at: datetime
    severity: str
    severity_number: int
    body: str
    exception_type: str = ""
    exception_message: str = ""


def log_lines(anchor: datetime) -> Iterator[LogLine]:
    for offset in range(SLACK, 60 - SLACK + 1):
        at = anchor - offset * MINUTE
        yield LogLine(PAYMENT, at, "ERROR", 17, "charge failed", EXCEPTION, EXCEPTION_MESSAGE)
        if offset % 5 == 0:
            yield LogLine(CHECKOUT, at, "INFO", 9, "order placed")


@dataclass(frozen=True, slots=True)
class SpanRow:
    service: str
    span_name: str
    span_id: str
    parent: str
    at: datetime
    duration_ms: float


def trace_spans(anchor: datetime) -> tuple[SpanRow, ...]:
    """One slow checkout request, its time mostly in payment's charge."""
    at = anchor - 15 * MINUTE
    return (
        SpanRow(CHECKOUT, SLOW_ENDPOINT, ROOT_SPAN, "", at, SLOW_MS),
        SpanRow(PAYMENT, CHARGE, CHILD_SPAN, ROOT_SPAN, at + timedelta(milliseconds=20), 850.0),
    )


@dataclass(frozen=True, slots=True)
class MetricPoint:
    # "gauge" or "sum" (an UpDownCounter, as host filesystem bytes arrive).
    table: Literal["gauge", "sum"]
    service: str
    name: str
    unit: str
    at: datetime
    value: float
    host: str = ""
    state: str = ""


def metric_points(anchor: datetime) -> Iterator[MetricPoint]:
    for offset in range(SLACK, 60 - SLACK + 1):
        at = anchor - offset * MINUTE
        yield MetricPoint("gauge", CHECKOUT, CUSTOM_METRIC, CUSTOM_UNIT, at, 14.0 + offset % 3)
        for host, (used, free) in DISKS.items():
            for state, value in (("used", used), ("free", free)):
                yield MetricPoint(
                    "sum", "hostmetrics", FILESYSTEM_METRIC, "By", at, float(value), host, state
                )


# --------------------------------------------------------------------------- Postgres


@dataclass(frozen=True, slots=True)
class ReleaseRow:
    service: str
    version: str
    offset: int


RELEASES: Final = (
    ReleaseRow(CHECKOUT, OLD_VERSION, 26 * 60),
    ReleaseRow(CHECKOUT, NEW_VERSION, 200),
)


@dataclass(frozen=True, slots=True)
class FindingRow:
    service: str
    severity: str
    started: int
    closed: int | None
    closed_reason: str | None
    title: str


FINDINGS: Final = (
    FindingRow(PAYMENT, "critical", 60 - SLACK, None, None, "payment is failing more than normal"),
    FindingRow(CHECKOUT, "warning", 25 * 60, 24 * 60, "recovered", "checkout was slower"),
)

# Each service's signals as the detection job writes them (`detection_state.signals`).
_JUDGED: Final = {signal: {"verdict": "normal"} for signal in ("slower", "failing", "traffic")}
LEARNING_DAYS: Final = 14


def detection_signals(service: str, anchor: datetime) -> dict[str, dict[str, str]]:
    if service == SEARCH:
        until = (anchor + timedelta(days=LEARNING_DAYS)).date().isoformat()
        return {
            signal: {"verdict": "learning", "learning_until": until}
            for signal in ("slower", "failing", "traffic")
        }
    if service == PAYMENT:
        return {**_JUDGED, "failing": {"verdict": "unusual"}}
    return dict(_JUDGED)


DETECTED: Final = (CHECKOUT, PAYMENT, INVENTORY, SEARCH)


@lru_cache(maxsize=4)
def _endpoint_rows(anchor: datetime) -> tuple[EndpointMinute, ...]:
    """The rollup rows at `anchor`, kept for the last few anchors: the answers sum them for
    every question."""
    return tuple(endpoint_minutes(anchor))


@lru_cache(maxsize=4)
def _edge_rows(anchor: datetime) -> tuple[EdgeMinute, ...]:
    return tuple(edge_minutes(anchor))


def sums(
    anchor: datetime,
    *,
    service: str,
    start: datetime,
    end: datetime,
    span_name: str | None = None,
    project: Project = "main",
) -> tuple[int, int]:
    """Requests and errors of a service (or one endpoint) in `[start, end)`, as the rollup
    sums them: by minute bucket."""
    requests = errors = 0
    for row in _endpoint_rows(anchor):
        if row.project != project or row.service != service:
            continue
        if span_name is not None and row.span_name != span_name:
            continue
        if start <= row.minute < end:
            requests += row.requests
            errors += row.errors
    return requests, errors


def edge_sums(
    anchor: datetime, *, client: str, server: str, start: datetime, end: datetime
) -> tuple[int, int]:
    calls = errors = 0
    for row in _edge_rows(anchor):
        if row.client == client and row.server == server and start <= row.minute < end:
            calls += row.calls
            errors += row.errors
    return calls, errors


def filesystem_utilization(host: str) -> float:
    used, free = DISKS[host]
    return round(used / (used + free), 2)
