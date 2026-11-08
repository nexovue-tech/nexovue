"""Reliability targets (F19): the arithmetic, the states and the labels, with no store.

Shared by the scheduler, which counts and judges, and the API, which shows, so a
screen and a message cannot disagree about a figure or a state (design, *Modules*).

- **Kinds and limits** (spec FR1 to FR6): what a target may be.
- **The allowance** (FR12): `(1 - objective) x events - failures`, rounded down.
- **The window** (FR9): the last 27 complete days plus today, by the project's zone,
  never before the source's first event.
- **The state** (FR10, FR10a): exactly one, the first that applies, with every other
  fact that holds as a qualifier; and the list's order.
- **The label** (D43): composed by nexovue from source, kind, objective and window,
  so a target's own name never enters an incident or a message.
- **Latency boundaries** (FR3): the worker's 63 bucket boundaries, moved here from
  detection's method because the API needs them too and cannot import the scheduler.
- **The suggestion** (FR7, FR8): plain arithmetic on four weeks, with no model (D10),
  and what a check's interval allows.

Every limit marked *a guess* is in the spec's register, *Cannot be proved before a customer*.
"""

from __future__ import annotations

import enum
import math
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Final, Literal, Self
from zoneinfo import ZoneInfo

from nexovue_platform.jobs import JobRequest

# ----------------------------------------------------------------------- kinds

SERVICE_AVAILABILITY: Final = "service_availability"
SERVICE_LATENCY: Final = "service_latency"
ENDPOINT_AVAILABILITY: Final = "endpoint_availability"
CHECK_AVAILABILITY: Final = "check_availability"
KINDS: Final = (SERVICE_AVAILABILITY, SERVICE_LATENCY, ENDPOINT_AVAILABILITY, CHECK_AVAILABILITY)
# Every kind is creatable (Step 2). Endpoint latency is no kind at all: the endpoint
# rollup holds a sampled quantile, not counts under a boundary (spec, *Out of scope*; AC4).
CREATABLE_KINDS: Final = KINDS
REQUEST_KINDS: Final = (SERVICE_AVAILABILITY, SERVICE_LATENCY, ENDPOINT_AVAILABILITY)

OBJECTIVE_LOWEST: Final = Decimal("90")
OBJECTIVE_HIGHEST: Final = Decimal("99.99")
OBJECTIVE_PLACES: Final = 2
WINDOWS_IN_DAYS: Final = (7, 28)
DEFAULT_WINDOW_DAYS: Final = 28
# FR6: protects the system, never sold as a tier (BR12). A guess until AC50.
TARGETS_PER_PROJECT: Final = 200
NAME_LONGEST: Final = 120
DESCRIPTION_LONGEST: Final = 500
SERVICE_NAME_LONGEST: Final = 255
ENDPOINT_NAME_LONGEST: Final = 255
# FR34: endpoints a service availability target may leave out (a guess).
LEFT_OUT_MOST: Final = 20
SAMPLING_ANSWERS: Final = ("no", "yes", "not_sure")

# ------------------------------------------------------------ queues and tasks

# On `scheduler-light`, capped under the shared floor (D66). Named here because the
# API queues a history read on create, and beat and the coordinator name the rest.
SLO_QUEUE: Final = "slo"
SLO_COUNTS_QUEUE: Final = "slo_counts"
SLO_HISTORY_QUEUE: Final = "slo_history"
JUDGE_SLOS_TASK: Final = "nexovue_scheduler.slos.judge_slos"
COUNT_SLOS_TASK: Final = "nexovue_scheduler.slos.count_slos"
READ_SLO_HISTORY_TASK: Final = "nexovue_scheduler.slos.read_slo_history"
# A history read takes up to this long (design, *What each job does*): soft, then hard.
HISTORY_SOFT_LIMIT_SECONDS: Final = 300
HISTORY_HARD_LIMIT_SECONDS: Final = 330

# A history job not started within a minute is overtaken by the next minute's hand-out,
# which picks up any target whose history is behind (F19 AC64).
HISTORY_EXPIRES_SECONDS: Final = 60


def history_request(org_id: object, target_id: object) -> JobRequest:
    """Read a target's past days: queued by the API on create, restore and a change of
    what is counted, and by the scheduler for any target still behind."""
    return JobRequest(
        task=READ_SLO_HISTORY_TASK,
        queue=SLO_HISTORY_QUEUE,
        kwargs={"target_id": str(target_id)},
        org_id=str(org_id),
        expires_seconds=HISTORY_EXPIRES_SECONDS,
        soft_limit_seconds=HISTORY_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=HISTORY_HARD_LIMIT_SECONDS,
    )


# --------------------------------------------------------------------- buckets

# `service_stats_1m.latency_buckets`: 64 counts over boundaries log-spaced from 1 ms
# to 60 s (apps/workers/internal/pipeline/derive/services.go). Edge i is 60000^(i/64)
# ms; bucket i lies between edges i and i + 1, so boundary i, the inner edge at the
# top of bucket i, is edge i + 1. "At or under boundary i" is buckets 0 to i.
BUCKET_COUNT: Final = 64
BOUNDARY_COUNT: Final = BUCKET_COUNT - 1
_CEILING_MS: Final = 60_000.0
LOG_EDGES: Final = tuple(
    math.log(_CEILING_MS) * index / BUCKET_COUNT for index in range(BUCKET_COUNT + 1)
)
# A typed boundary read back from a float may sit a hair above its own edge.
_BOUNDARY_TOLERANCE: Final = 1e-9


def boundary_ms(index: int) -> float:
    """Boundary `index`, 0 to 62: about 1.19 ms to about 50.5 s."""
    if not 0 <= index < BOUNDARY_COUNT:
        raise ValueError(f"no latency boundary {index}")
    return math.exp(LOG_EDGES[index + 1])


def boundary_at_or_under(asked_ms: float) -> int | None:
    """The highest boundary at or under what a person typed (FR3, BR3: never
    interpolated); None below the first. Above the last, the last."""
    if asked_ms <= 0:
        return None
    position = math.log(asked_ms) / math.log(_CEILING_MS) * BUCKET_COUNT
    index = math.floor(position + _BOUNDARY_TOLERANCE) - 1
    if index < 0:
        return None
    return min(index, BOUNDARY_COUNT - 1)


LOWEST_BOUNDARY_MS: Final = boundary_ms(0)
HIGHEST_BOUNDARY_MS: Final = boundary_ms(BOUNDARY_COUNT - 1)


def latency_refusal(asked_ms: float) -> str | None:
    """Why a typed threshold is refused, beside the field; None when a boundary holds it.
    Above the highest boundary is refused, not snapped to it (AC101)."""
    if not math.isfinite(asked_ms) or asked_ms < LOWEST_BOUNDARY_MS:
        return f"The finest threshold nexovue counts is {LOWEST_BOUNDARY_MS:.1f} ms."
    if asked_ms > HIGHEST_BOUNDARY_MS * (1 + _BOUNDARY_TOLERANCE):
        return f"The highest threshold nexovue counts is {HIGHEST_BOUNDARY_MS / 1000:.1f} s."
    return None


def at_or_under(buckets: Sequence[int], index: int) -> int:
    """Requests at or under boundary `index`: buckets 0 to `index` (design, *Reading
    latency from buckets*)."""
    return sum(buckets[: index + 1])


def boundary_holding(buckets: Sequence[int], share: Decimal) -> int | None:
    """The lowest boundary at or under which at least `share` percent of the requests
    fell; None with no requests, or when only the open top bucket reaches it."""
    total = sum(buckets)
    if total <= 0:
        return None
    needed = share * total / Decimal(100)
    held = 0
    for index in range(BOUNDARY_COUNT):
        held += buckets[index]
        if held >= needed:
            return index
    return None


# ----------------------------------------------------------- health-check names

# FR34 pre-ticks endpoints that look like a probe, so nothing is left out unseen. The
# words are a guess at common probe paths, matched as a whole segment of the name.
_HEALTH_CHECK: Final = re.compile(
    r"(?:^|[\s/._-])(?:health(?:z|check)?|readyz?|readiness|livez?|liveness|ping)(?:$|[\s/._-])",
    re.IGNORECASE,
)


def looks_like_health_check(endpoint: str) -> bool:
    return _HEALTH_CHECK.search(endpoint) is not None


# -------------------------------------------------------------------- objective


def objective_refusal(objective: Decimal) -> str | None:
    """Why an objective is refused, beside the field (FR2); None when it is allowed."""
    if objective.as_tuple().exponent < -OBJECTIVE_PLACES:  # type: ignore[operator]
        return (
            "Use at most two decimals. At 99.999% over 28 days a whole outage of about "
            "24 seconds spends it, and nexovue counts by the minute."
        )
    if not OBJECTIVE_LOWEST <= objective <= OBJECTIVE_HIGHEST:
        return "Choose a target between 90% and 99.99%."
    return None


def objective_text(objective: Decimal) -> str:
    """99.90 as "99.9", 99.00 as "99"."""
    return format(objective.normalize(), "f")


# -------------------------------------------------------------------- allowance


def allowed_failures(objective: Decimal, total: int) -> int:
    """What the objective allows over `total` events, rounded down (FR12)."""
    allowed = (Decimal(100) - objective) * total / Decimal(100)
    return int(allowed.to_integral_value(rounding=ROUND_FLOOR))


def allowance_left(objective: Decimal, total: int, failures: int) -> int:
    """What is still allowed; negative once the target is missed."""
    return allowed_failures(objective, total) - failures


def shown_allowance(left: int) -> int:
    """Never shown below zero (FR12)."""
    return max(left, 0)


def figure_percent(good: int, total: int) -> Decimal | None:
    """The share of good events as a percentage, to two places, rounded towards
    failing, so a missed target never prints at or above its objective (FR10)."""
    if total <= 0:
        return None
    share = Decimal(good) * 100 / Decimal(total)
    return share.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


_SIGNAL_OF: Final[Mapping[str, Literal["availability", "latency", "check"]]] = {
    SERVICE_AVAILABILITY: "availability",
    ENDPOINT_AVAILABILITY: "availability",
    SERVICE_LATENCY: "latency",
    CHECK_AVAILABILITY: "check",
}
_ALLOWANCE_WORDS: Final = {
    "availability": "Failures still allowed",
    "latency": "Slow requests still allowed",
    "check": "Failed runs still allowed",
}
_FAILURE_NOUN: Final = {
    "availability": "failures",
    "latency": "slow requests",
    "check": "failed runs",
}
_EVENT_NOUN: Final = {"availability": "requests", "latency": "requests", "check": "runs"}


def allowance_words(kind: str) -> str:
    """FR12's words by kind: a latency target never says "failures" (AC89)."""
    return _ALLOWANCE_WORDS[_SIGNAL_OF[kind]]


def failure_noun(kind: str) -> str:
    return _FAILURE_NOUN[_SIGNAL_OF[kind]]


def event_noun(kind: str) -> str:
    return _EVENT_NOUN[_SIGNAL_OF[kind]]


# ------------------------------------------------------------ label and identity


def subject_label(
    kind: str,
    *,
    objective: Decimal,
    window_days: int,
    service_name: str | None = None,
    endpoint_name: str | None = None,
    check_name: str | None = None,
    boundary_index: int | None = None,
) -> str:
    """A label such as "checkout availability, 99.9% over 28 days": source, kind,
    objective and window, with no free text a person typed (D43, FR33)."""
    tail = f"{objective_text(objective)}% over {window_days} days"
    if kind == SERVICE_AVAILABILITY:
        return f"{service_name} availability, {tail}"
    if kind == ENDPOINT_AVAILABILITY:
        return f"{service_name} {endpoint_name} availability, {tail}"
    if kind == SERVICE_LATENCY:
        if boundary_index is None:
            raise ValueError("a latency target needs its boundary")
        return f"{service_name} latency under {boundary_ms(boundary_index):.1f} ms, {tail}"
    if kind == CHECK_AVAILABILITY:
        return f"{check_name} availability, {tail}"
    raise ValueError(f"no target kind {kind!r}")


def definition_key(
    kind: str,
    *,
    objective: Decimal,
    window_days: int,
    service_name: str | None = None,
    endpoint_name: str | None = None,
    check_id: object | None = None,
    boundary_index: int | None = None,
) -> str:
    """What two targets with one definition share (D35): one text value, so a null
    field causes no trouble in the unique index. The objective is normalised, so
    99.9 and 99.90 are the same target."""
    parts = (
        kind,
        service_name or "",
        endpoint_name or "",
        "" if check_id is None else str(check_id),
        "" if boundary_index is None else str(boundary_index),
        objective_text(objective),
        str(window_days),
    )
    # A unit separator cannot be typed into a name, so no two definitions join alike.
    return "\x1f".join(parts)


# ----------------------------------------------------------------------- days


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """A local day's start and end in UTC: 23 or 25 hours on a clock change. In UTC,
    because subtracting two times in one zone measures the wall clock, not the day."""
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def window_days_of(today: date, window_days: int, *, first_event_day: date | None) -> list[date]:
    """The window's days (FR9): the last `window_days - 1` complete days and today,
    never before the source's first event."""
    first = today - timedelta(days=window_days - 1)
    if first_event_day is not None and first_event_day > first:
        first = first_event_day
    return [first + timedelta(days=offset) for offset in range((today - first).days + 1)]


@dataclass(frozen=True, slots=True)
class DayCounts:
    """One stored day of one target, as far as a window needs it."""

    good: int
    total: int
    lost_minutes: int
    excluded_minutes: int = 0
    # A check's runs (FR11): what its interval expected, what never came, and what ran
    # in a maintenance window. None for a request kind.
    expected_runs: int | None = None
    missed_runs: int | None = None
    maintenance_runs: int | None = None


@dataclass(frozen=True, slots=True)
class WindowFigures:
    """A window summed from stored days (D2, D4): by whole days, from Postgres alone."""

    good: int
    total: int
    failures: int
    days_held: int
    lost_minutes: int
    excluded_minutes: int
    # Measured minutes over the window's minutes less excluded ones, from the first
    # event (FR11); for a check, runs measured over runs expected outside maintenance.
    # None when nothing of the window can be measured yet.
    coverage: float | None


def window_figures(
    days: Mapping[date, DayCounts],
    *,
    window_days: int,
    first_event_at: datetime | None,
    now: datetime,
    zone: ZoneInfo,
) -> WindowFigures:
    """Sum the days a window holds. A day with no row adds nothing, not zeros: it is
    not yet counted (AC1)."""
    today = now.astimezone(zone).date()
    first_day = first_event_at.astimezone(zone).date() if first_event_at else None
    held = [
        (day, days[day])
        for day in window_days_of(today, window_days, first_event_day=first_day)
        if day in days
    ]
    good = sum(counts.good for _, counts in held)
    total = sum(counts.total for _, counts in held)
    lost = sum(counts.lost_minutes for _, counts in held)
    excluded = sum(counts.excluded_minutes for _, counts in held)
    considered = sum(
        _minutes_considered(day, zone, first_event_at=first_event_at, now=now) for day, _ in held
    )
    judged = considered - excluded
    coverage = max(0.0, (judged - lost) / judged) if judged > 0 else None
    if any(counts.expected_runs is not None for _, counts in held):
        coverage = _run_coverage([counts for _, counts in held])
    return WindowFigures(
        good=good,
        total=total,
        failures=total - good,
        days_held=len(held),
        lost_minutes=lost,
        excluded_minutes=excluded,
        coverage=coverage,
    )


def _run_coverage(days: Sequence[DayCounts]) -> float | None:
    """A check's coverage (FR11): missed runs lower it; runs in a maintenance window are
    left out of it, as excluded time is (BR6)."""
    expected = sum((day.expected_runs or 0) - (day.maintenance_runs or 0) for day in days)
    missed = sum(day.missed_runs or 0 for day in days)
    if expected <= 0:
        return None
    return max(0.0, (expected - missed) / expected)


def _minutes_considered(
    day: date, zone: ZoneInfo, *, first_event_at: datetime | None, now: datetime
) -> int:
    """The minutes of `day` a window counts: from the first event, up to now."""
    start, end = day_bounds(day, zone)
    if first_event_at is not None:
        start = max(start, first_event_at)
    end = min(end, now)
    return max(0, int((end - start).total_seconds() // 60))


# ---------------------------------------------------------------------- states

# FR10's guesses, each in the spec's register.
STALE_AFTER: Final = timedelta(minutes=15)
QUIET_AFTER: Final = timedelta(minutes=15)
COVERAGE_TO_JUDGE: Final = 0.9
THIN_ALLOWED: Final = 10
BUILDING_SHARE_OF_WINDOW: Final = 4
CHECK_SILENT_INTERVALS: Final = 3
# FR10a: a silence counts once it passes the larger of 15 minutes and twice the
# usual longest quiet stretch, capped at a day.
_ATTENTION_FLOOR_MINUTES: Final = 15
_ATTENTION_CAP_MINUTES: Final = 24 * 60
_USUAL_QUIET_FACTOR: Final = 2


class State(enum.StrEnum):
    """FR10's states, in the order they are tried."""

    DISABLED = "disabled"
    NOT_UP_TO_DATE = "not_up_to_date"
    SOURCE_GONE = "source_gone"
    RUNNING_OUT = "running_out"
    COUNTING = "counting"
    NOT_MEASURED = "not_measured"
    NO_RECENT_REQUESTS = "no_recent_requests"
    BUILDING_HISTORY = "building_history"
    NOTHING_LEFT = "nothing_left"
    TOO_LITTLE_TRAFFIC = "too_little_traffic"
    ON_TRACK = "on_track"


class Qualifier(enum.StrEnum):
    """A fact that also holds, shown under the sentence, in this order (design,
    *One target*: not up to date; no alerts; snoozed; missed; thin traffic; the rest)."""

    RUNNING_OUT = "running_out"
    NOT_UP_TO_DATE = "not_up_to_date"
    NO_ALERTS = "no_alerts"
    SNOOZED = "snoozed"
    MISSED = "missed"
    THIN_TRAFFIC = "thin_traffic"
    NOT_MEASURED = "not_measured"
    NO_RECENT_REQUESTS = "no_recent_requests"
    BUILDING_HISTORY = "building_history"
    NEVER_FAILED = "never_failed"


class Variant(enum.StrEnum):
    """Which of a state's sentences applies (FR10's sentence shapes)."""

    PLAIN = "plain"
    WAS_BURNING = "was_burning"
    NOTHING_LEFT = "nothing_left"
    RESOLVING = "resolving"
    NOT_JUDGED = "not_judged"
    EXACTLY_ON_TARGET = "exactly_on_target"
    MISSED = "missed"


# The states the headline counts (FR10a). No recent requests counts only past the limit.
_ALWAYS_COUNTED: Final = frozenset(
    {
        State.RUNNING_OUT,
        State.NOTHING_LEFT,
        State.NOT_MEASURED,
        State.NOT_UP_TO_DATE,
        State.SOURCE_GONE,
    }
)


@dataclass(frozen=True, slots=True)
class Burn:
    """A tier holding, or an incident still open, as `slo_status` stores it (Step 4)."""

    severity: Literal["critical", "warning"]
    # The long-window rate of the highest tier holding, in times faster than allowed.
    rate: float
    since: datetime
    # How long what is left lasts at the failures per hour seen; None when unknown.
    hours_left: float | None
    resolving: bool = False
    # False when no tier can judge any more ("Was using … until 02:41. Not judged since.").
    judged: bool = True


@dataclass(frozen=True, slots=True)
class Facts:
    """What a target's state is decided from: its row, its status and its window."""

    kind: str
    window_days: int
    enabled: bool
    now: datetime
    # The last minute the job worked this target out; None before its first run.
    evaluated_minute: datetime | None
    # Since when the job has owed it a first answer: the target's creation. A target
    # never worked out is Not up to date only once that is 15 minutes old.
    expected_since: datetime
    # History still being read: the definition is newer than what was counted.
    counting: bool
    source_gone: bool
    burn: Burn | None
    coverage: float | None
    last_event_minute: datetime | None
    days_held: int
    allowed: int
    failures: int
    # Whether a rule sends its burns anywhere; None before burn alerts exist (Step 4).
    alerting: bool | None
    snoozed: bool
    longest_quiet_7d_minutes: int
    traffic_finding_open: bool
    check_interval_seconds: int | None

    def replace(self, **changes: object) -> Self:
        return replace(self, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Status:
    state: State
    variant: Variant
    qualifiers: tuple[Qualifier, ...]
    # Counted in the list's headline (FR10a).
    attention: bool
    severity: Literal["critical", "warning"] | None
    hours_left: float | None
    # Minutes since the last event, when that is what the state is about.
    quiet_minutes: int | None


def status_of(facts: Facts) -> Status:
    """FR10: the first state that applies, and every other fact as a qualifier."""
    facts_holding = _facts_holding(facts)
    state = next(state for state in State if state in facts_holding)
    variant = _variant(state, facts)
    qualifiers = tuple(
        qualifier
        for qualifier in Qualifier
        if qualifier in _QUALIFIER_FACTS
        and _QUALIFIER_FACTS[qualifier] in facts_holding
        and _QUALIFIER_FACTS[qualifier] is not state
    ) + tuple(_extra_qualifiers(state, facts))
    qualifiers = tuple(sorted(set(qualifiers), key=list(Qualifier).index))
    quiet = _quiet_minutes(facts)
    attention = state in _ALWAYS_COUNTED or (
        state is State.NO_RECENT_REQUESTS and _past_attention_limit(facts, quiet)
    )
    burn = facts.burn
    return Status(
        state=state,
        variant=variant,
        qualifiers=qualifiers,
        attention=attention,
        severity=burn.severity if burn is not None else None,
        hours_left=burn.hours_left if burn is not None else None,
        quiet_minutes=quiet,
    )


# A qualifier that restates a state, when that state is not the one shown.
_QUALIFIER_FACTS: Final = {
    Qualifier.RUNNING_OUT: State.RUNNING_OUT,
    Qualifier.NOT_UP_TO_DATE: State.NOT_UP_TO_DATE,
    Qualifier.NOT_MEASURED: State.NOT_MEASURED,
    Qualifier.NO_RECENT_REQUESTS: State.NO_RECENT_REQUESTS,
    Qualifier.BUILDING_HISTORY: State.BUILDING_HISTORY,
}


def _facts_holding(facts: Facts) -> frozenset[State]:
    holding = {State.ON_TRACK}
    if not facts.enabled:
        holding.add(State.DISABLED)
    last_answer = facts.evaluated_minute or facts.expected_since
    if facts.now - last_answer > STALE_AFTER:
        holding.add(State.NOT_UP_TO_DATE)
    if facts.source_gone:
        holding.add(State.SOURCE_GONE)
    if facts.burn is not None:
        holding.add(State.RUNNING_OUT)
    if facts.counting:
        holding.add(State.COUNTING)
    if facts.coverage is not None and facts.coverage < COVERAGE_TO_JUDGE:
        holding.add(State.NOT_MEASURED)
    if _silent(facts):
        holding.add(State.NO_RECENT_REQUESTS)
    if facts.days_held < math.ceil(facts.window_days / BUILDING_SHARE_OF_WINDOW):
        holding.add(State.BUILDING_HISTORY)
    if facts.failures >= facts.allowed:
        holding.add(State.NOTHING_LEFT)
    if facts.allowed < THIN_ALLOWED:
        holding.add(State.TOO_LITTLE_TRAFFIC)
    return frozenset(holding)


def _extra_qualifiers(state: State, facts: Facts) -> Iterable[Qualifier]:
    if facts.alerting is False:
        yield Qualifier.NO_ALERTS
    if facts.snoozed:
        yield Qualifier.SNOOZED
    if facts.failures > facts.allowed and state is not State.NOTHING_LEFT:
        yield Qualifier.MISSED
    if facts.allowed < THIN_ALLOWED and state is not State.TOO_LITTLE_TRAFFIC:
        yield Qualifier.THIN_TRAFFIC
    if facts.failures == 0 and facts.kind != CHECK_AVAILABILITY:
        yield Qualifier.NEVER_FAILED


def _variant(state: State, facts: Facts) -> Variant:
    burn = facts.burn
    if state is State.NOT_UP_TO_DATE and burn is not None:
        return Variant.WAS_BURNING
    if state is State.RUNNING_OUT and burn is not None:
        if not burn.judged:
            return Variant.NOT_JUDGED
        if burn.resolving:
            return Variant.RESOLVING
        if facts.failures >= facts.allowed:
            return Variant.NOTHING_LEFT
    if state is State.NOTHING_LEFT:
        return Variant.MISSED if facts.failures > facts.allowed else Variant.EXACTLY_ON_TARGET
    return Variant.PLAIN


def _silence_limit(facts: Facts) -> timedelta:
    if facts.kind == CHECK_AVAILABILITY and facts.check_interval_seconds:
        return timedelta(seconds=facts.check_interval_seconds * CHECK_SILENT_INTERVALS)
    return QUIET_AFTER


def _silent(facts: Facts) -> bool:
    last = facts.last_event_minute
    return last is not None and facts.now - last > _silence_limit(facts)


def _quiet_minutes(facts: Facts) -> int | None:
    if facts.last_event_minute is None or not _silent(facts):
        return None
    return int((facts.now - facts.last_event_minute).total_seconds() // 60)


def _past_attention_limit(facts: Facts, quiet: int | None) -> bool:
    if facts.traffic_finding_open:
        return True
    if quiet is None:
        return False
    limit = max(_ATTENTION_FLOOR_MINUTES, _USUAL_QUIET_FACTOR * facts.longest_quiet_7d_minutes)
    return quiet > min(limit, _ATTENTION_CAP_MINUTES)


# ----------------------------------------------------------------------- order


def ordered[Row](rows: Sequence[tuple[Row, Status]]) -> list[tuple[Row, Status]]:
    """FR10a's order: counted rows first, so the top N are the headline's N; among
    them critical, then warning, then shortest time left; then the other states;
    Disabled last. Ties keep the order given."""

    def key(row: tuple[Row, Status]) -> tuple[int, int, float]:
        status = row[1]
        if status.state is State.DISABLED:
            return (2, 0, 0.0)
        if not status.attention:
            return (1, 0, 0.0)
        severity = {"critical": 0, "warning": 1}.get(status.severity or "", 2)
        left = status.hours_left if status.hours_left is not None else math.inf
        return (0, severity, left)

    return sorted(rows, key=key)


# ------------------------------------------------------------------ suggestion

# FR7: the worst week rounds down to the next of these.
SUGGESTION_STEPS: Final = tuple(
    Decimal(step) for step in ("90", "95", "97", "98", "99", "99.5", "99.9", "99.95")
)
SUGGESTION_WEEKS: Final = 4
DAYS_IN_A_WEEK: Final = 7
# FR8: fewer days than this offer a labelled starting guess (D34; the 99.5 is a guess).
SUGGESTION_DAYS_NEEDED: Final = 7
STARTER_OBJECTIVE: Final = Decimal("99.5")
# FR7: a latency suggestion is the boundary that held this share of requests, and the
# target it proposes is that share.
LATENCY_SUGGESTED_SHARE: Final = Decimal("95")


class Suggested(enum.StrEnum):
    """Where a suggestion came from, or why there is none (FR7, FR8)."""

    FROM_WORST_WEEK = "from_worst_week"
    # "Your worst week would have missed this."
    FROM_TYPICAL_WEEK = "from_typical_week"
    # "A starting guess, not from your data."
    STARTER = "starter"
    # Worst and typical weeks both below 90%: no suggestion, and why.
    BELOW_LOWEST = "below_lowest"
    # Too few events for the suggestion to be measurable (FR12): none; offer a check.
    TOO_FEW = "too_few"


@dataclass(frozen=True, slots=True)
class Week:
    """One 7-day window's events: requests or a check's counted runs."""

    good: int
    total: int


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A suggestion and the arithmetic behind it, so the screen can show it (FR7)."""

    outcome: Suggested
    objective: Decimal | None
    # Each week's figure, newest first; None for a week with no events.
    weeks: tuple[Decimal | None, ...]
    worst: Decimal | None = None
    typical: Decimal | None = None
    best: Decimal | None = None
    # A latency suggestion's boundary, and each week's boundary holding 95%.
    boundary_index: int | None = None
    week_boundaries: tuple[int | None, ...] = ()


def suggest_availability(weeks: Sequence[Week], *, days_of_data: int) -> Suggestion:
    """FR7 for availability, and for a check's runs: the worst of four weeks rounded down
    to a step, or the typical week when the worst is more than a step below it."""
    figures = tuple(figure_percent(week.good, week.total) for week in weeks)
    if days_of_data < SUGGESTION_DAYS_NEEDED:
        return Suggestion(Suggested.STARTER, STARTER_OBJECTIVE, figures)
    measured = sorted(figure for figure in figures if figure is not None)
    if not measured:
        return Suggestion(Suggested.TOO_FEW, None, figures)
    worst, best = measured[0], measured[-1]
    typical = Decimal(statistics.median(measured)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def suggested(outcome: Suggested, objective: Decimal | None = None) -> Suggestion:
        return Suggestion(outcome, objective, figures, worst=worst, typical=typical, best=best)

    worst_step, typical_step = _step_at_or_under(worst), _step_at_or_under(typical)
    if typical_step is None:
        return suggested(Suggested.BELOW_LOWEST)
    if worst_step is None or typical_step - worst_step > 1:
        outcome, step = Suggested.FROM_TYPICAL_WEEK, typical_step
    else:
        outcome, step = Suggested.FROM_WORST_WEEK, worst_step
    objective = SUGGESTION_STEPS[step]
    if allowed_failures(objective, sum(week.total for week in weeks)) < THIN_ALLOWED:
        return suggested(Suggested.TOO_FEW)
    return suggested(outcome, objective)


def suggest_latency(weeks: Sequence[Sequence[int]], *, days_of_data: int) -> Suggestion:
    """FR7 for latency: the lowest boundary that held 95% of requests in every week held,
    so the worst week sets it, as it does for availability."""
    holding = tuple(boundary_holding(buckets, LATENCY_SUGGESTED_SHARE) for buckets in weeks)
    if days_of_data < SUGGESTION_DAYS_NEEDED:
        return Suggestion(Suggested.STARTER, STARTER_OBJECTIVE, (), week_boundaries=holding)
    total = sum(sum(buckets) for buckets in weeks)
    held = [index for index in holding if index is not None]
    if not held or allowed_failures(LATENCY_SUGGESTED_SHARE, total) < THIN_ALLOWED:
        return Suggestion(Suggested.TOO_FEW, None, (), week_boundaries=holding)
    return Suggestion(
        Suggested.FROM_WORST_WEEK,
        LATENCY_SUGGESTED_SHARE,
        (),
        boundary_index=max(held),
        week_boundaries=holding,
    )


def _step_at_or_under(figure: Decimal) -> int | None:
    """The index of the highest step at or under `figure`; None below the lowest."""
    under = [index for index, step in enumerate(SUGGESTION_STEPS) if step <= figure]
    return under[-1] if under else None


# -------------------------------------------------------------- check intervals

# The checks table's own floor (`interval_is_within_bounds`): "run it every minute".
CHECK_FASTEST_INTERVAL_SECONDS: Final = 60
_SECONDS_IN_A_DAY: Final = 86_400
# FR16: a check tier needs 10 runs in its long window, so slower than 2.4 hours a run
# no tier can judge it (a guess, in the register).
CHECK_RUNS_TO_JUDGE: Final = 10
CHECK_SLOWEST_JUDGED_SECONDS: Final = _SECONDS_IN_A_DAY // CHECK_RUNS_TO_JUDGE


def expected_runs(interval_seconds: int, *, window_days: int, locations: int = 1) -> int:
    """Runs a check's interval expects over a window, from each location it runs at."""
    return window_days * _SECONDS_IN_A_DAY // interval_seconds * locations


def alerts_can_judge_interval(interval_seconds: int) -> bool:
    return interval_seconds <= CHECK_SLOWEST_JUDGED_SECONDS


# ----------------------------------------------------------- facts from storage


@dataclass(frozen=True, slots=True)
class StoredTarget:
    """What `slo_targets` and `slo_status` hold for one target, as the list reads them."""

    kind: str
    objective: Decimal
    window_days: int
    enabled: bool
    first_event_at: datetime | None
    definition_version: int
    history_read_version: int
    # A check target whose check was deleted (AC70).
    source_missing: bool
    evaluated_minute: datetime | None
    last_event_minute: datetime | None
    longest_quiet_7d_minutes: int
    created_at: datetime


def facts_from_storage(
    target: StoredTarget,
    days: Mapping[date, DayCounts],
    *,
    now: datetime,
    zone: ZoneInfo,
    check_interval_seconds: int | None = None,
) -> tuple[Facts, WindowFigures]:
    """FR10's facts for a target from Postgres alone (NFR3, AC40): its row, its status
    and its stored days. Burn, rules and snoozes join in Step 4."""
    figures = window_figures(
        days,
        window_days=target.window_days,
        first_event_at=target.first_event_at,
        now=now,
        zone=zone,
    )
    counting = target.history_read_version < target.definition_version
    window_start = day_bounds(
        window_days_of(now.astimezone(zone).date(), target.window_days, first_event_day=None)[0],
        zone,
    )[0]
    last = target.last_event_minute
    source_gone = target.source_missing or (not counting and (last is None or last < window_start))
    facts = Facts(
        kind=target.kind,
        window_days=target.window_days,
        enabled=target.enabled,
        now=now,
        evaluated_minute=target.evaluated_minute,
        expected_since=target.created_at,
        counting=counting,
        source_gone=source_gone,
        burn=None,
        coverage=figures.coverage,
        last_event_minute=last,
        days_held=figures.days_held,
        allowed=allowed_failures(target.objective, figures.total),
        failures=figures.failures,
        alerting=None,
        snoozed=False,
        longest_quiet_7d_minutes=target.longest_quiet_7d_minutes,
        traffic_finding_open=False,
        check_interval_seconds=check_interval_seconds,
    )
    return facts, figures
