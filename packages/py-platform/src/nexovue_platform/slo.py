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
from typing import Final, Literal, Protocol, Self
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

# FR31: a deleted target can be restored, and its daily figures stay, this long (a guess).
# Then the scheduler purges it, keeping each complete month's result (D46).
RESTORE_FOR: Final = timedelta(days=30)
# How a target whose check was deleted is still labelled, by what it was (D43).
DELETED_CHECK_NAME: Final = "deleted check"

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
    """One stored day of one target: what a window and a month need of it."""

    good: int
    total: int
    lost_minutes: int
    excluded_minutes: int = 0
    # A check's runs (FR11): what its interval expected, what never came, and what ran
    # in a maintenance window. None for a request kind.
    expected_runs: int | None = None
    missed_runs: int | None = None
    maintenance_runs: int | None = None
    # The day's quiet time and stretches, so a month's longest crosses midnight.
    quiet_minutes: int = 0
    quiet_leading_minutes: int = 0
    quiet_trailing_minutes: int = 0
    longest_quiet_minutes: int = 0
    # False when the day was first read after nexovue's loss records lapsed (FR28).
    loss_known: bool = True
    final: bool = False
    definition_version: int = 0
    # When a person's change rewrote the day once it was final, and why (BR13).
    changed_at: datetime | None = None
    changed_why: str | None = None

    def replace(self, **changes: object) -> Self:
        return replace(self, **changes)  # type: ignore[arg-type]

    @classmethod
    def of_row(cls, row: object) -> Self:
        """A `slo_daily_counts` row, read by the API's model or the scheduler's statement."""
        return cls(**{name: getattr(row, name) for name in _DAY_COLUMNS})


_DAY_COLUMNS: Final = tuple(DayCounts.__dataclass_fields__)


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
    return _summed(held, first_event_at=first_event_at, now=now, zone=zone)


def _summed(
    held: Sequence[tuple[date, DayCounts]],
    *,
    first_event_at: datetime | None,
    now: datetime,
    zone: ZoneInfo,
) -> WindowFigures:
    """The days held, summed: a window's or a month's figures and coverage (FR11)."""
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


def burn_from_status(tiers: Mapping[str, object], *, burning_since: datetime | None) -> Burn | None:
    """FR10's Running out from what the job stored in `slo_status` (Step 4): a burn while
    `burning_since` is set, with its last held severity and rate. Resolving when it no
    longer holds but a tier judges; not judged when no tier can (FR17)."""
    held = tiers.get("burn")
    if burning_since is None or not isinstance(held, Mapping):
        return None
    by_tier = tiers.get("by_tier")
    judged_tiers = list(by_tier.values()) if isinstance(by_tier, Mapping) else []
    holding = any(isinstance(each, Mapping) and each.get("holds") for each in judged_tiers)
    judging = any(isinstance(each, Mapping) and each.get("can_judge") for each in judged_tiers)
    severity: Literal["critical", "warning"] = (
        "critical" if held.get("severity") == "critical" else "warning"
    )
    rate = held.get("rate")
    hours_left = held.get("hours_left")
    return Burn(
        severity=severity,
        rate=float(rate) if isinstance(rate, int | float) else 0.0,
        since=burning_since,
        hours_left=float(hours_left) if holding and isinstance(hours_left, int | float) else None,
        resolving=judging and not holding,
        judged=judging,
    )


def facts_from_storage(
    target: StoredTarget,
    days: Mapping[date, DayCounts],
    *,
    now: datetime,
    zone: ZoneInfo,
    check_interval_seconds: int | None = None,
    burn: Burn | None = None,
    alerting: bool | None = None,
    snoozed: bool = False,
) -> tuple[Facts, WindowFigures]:
    """FR10's facts for a target from Postgres alone (NFR3, AC40): its row, its status
    and its stored days, with the burn its status holds and whether a rule sends it
    anywhere (Step 4)."""
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
        burn=burn,
        coverage=figures.coverage,
        last_event_minute=last,
        days_held=figures.days_held,
        allowed=allowed_failures(target.objective, figures.total),
        failures=figures.failures,
        alerting=alerting,
        snoozed=snoozed,
        longest_quiet_7d_minutes=target.longest_quiet_7d_minutes,
        traffic_finding_open=False,
        check_interval_seconds=check_interval_seconds,
    )
    return facts, figures


# ---------------------------------------------------------------- quiet stretches


class QuietDay(Protocol):
    """A stored day's quiet time: a scheduler's counted row or the API's stored one."""

    @property
    def total(self) -> int: ...
    @property
    def lost_minutes(self) -> int: ...
    @property
    def quiet_minutes(self) -> int: ...
    @property
    def quiet_leading_minutes(self) -> int: ...
    @property
    def quiet_trailing_minutes(self) -> int: ...
    @property
    def longest_quiet_minutes(self) -> int: ...


def longest_quiet(days: Sequence[QuietDay]) -> int:
    """The longest quiet stretch across consecutive days, joining one day's trailing
    stretch to the next day's leading one, so a stretch can cross midnight (design,
    *Status and days*). A whole quiet day carries the stretch on."""
    longest = carried = 0
    for day in days:
        if (
            day.quiet_leading_minutes == day.quiet_minutes
            and day.total == 0
            and day.lost_minutes == 0
        ):
            carried += day.quiet_minutes
            longest = max(longest, carried)
            continue
        longest = max(longest, day.longest_quiet_minutes, carried + day.quiet_leading_minutes)
        carried = day.quiet_trailing_minutes
    return longest


# ------------------------------------------------------------------------ months

# FR27: months build up from the first day held, to this many, the current one
# included; daily rows lapse at 400 days, which holds them (founder decision 20).
MONTHS_SHOWN: Final = 13
# BR13: a span's own time is trusted 7 days back, so an older day is final.
FINAL_AFTER_DAYS: Final = 7
# `intake_refusals` and `detection_events` are kept 29 days: a day first read later
# cannot say what nexovue lost in it (FR28).
LOSS_RECORDS_DAYS: Final = 29
# FR27c: a day at least this far below its usual weekday is a sharp fall (a guess),
# against the median of the same weekday in up to this many earlier weeks held.
SHARP_FALL_PERCENT: Final = Decimal("50")
SHARP_FALL_WEEKS_COMPARED: Final = SUGGESTION_WEEKS
# `slo_daily_counts.changed_why` when a definition change rewrote a final day.
CHANGED_BY_DEFINITION: Final = "definition"
CHANGED_BY_EXCLUSION: Final = "exclusion"
CHANGED_BY_EXCLUSION_REMOVED: Final = "exclusion_removed"


class MonthResult(enum.StrEnum):
    """A month against its objective (FR27b): met, missed, or not judged below 90%."""

    MET = "met"
    MISSED = "missed"
    NOT_JUDGED = "not_judged"


@dataclass(frozen=True, slots=True)
class SharpFall:
    """FR27c: a day whose requests fell this far below its usual weekday."""

    day: date
    fall_percent: int


@dataclass(frozen=True, slots=True)
class MonthFigures:
    """One local month of one target, from its stored days alone (FR27, AC44)."""

    month: date
    result: MonthResult
    figure_percent: Decimal | None
    good: int
    total: int
    failures: int
    coverage: float | None
    quiet_minutes: int
    longest_quiet_minutes: int
    lost_minutes: int
    excluded_minutes: int
    # A check's runs (FR27): none for a request kind.
    missed_runs: int | None
    maintenance_runs: int | None
    days_held: int
    days_in_month: int
    # Any day under 7 days old (BR13).
    may_still_change: bool
    # False when any day was first read after nexovue's loss records lapsed (AC69).
    loss_known: bool
    # The latest change a person made to one of its days after the month ended.
    changed_at: datetime | None
    changed_why: str | None
    # The last day still counted under an earlier definition (AC45); None when none is.
    earlier_definition_through: date | None
    sharp_falls: tuple[SharpFall, ...]


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def previous_month(month: date) -> date:
    return month_start(month - timedelta(days=1))


def days_in_month(month: date) -> int:
    return (next_month(month) - month).days


def month_result(
    objective: Decimal, *, total: int, failures: int, coverage: float | None
) -> MonthResult:
    """Not judged below 90% coverage or with nothing counted (FR27), else met while
    something is still allowed or exactly on the target."""
    if coverage is None or coverage < COVERAGE_TO_JUDGE or total <= 0:
        return MonthResult.NOT_JUDGED
    if allowance_left(objective, total, failures) >= 0:
        return MonthResult.MET
    return MonthResult.MISSED


def month_figures(
    days: Mapping[date, DayCounts],
    *,
    month: date,
    kind: str,
    objective: Decimal,
    definition_version: int,
    first_event_at: datetime | None,
    now: datetime,
    zone: ZoneInfo,
) -> MonthFigures | None:
    """A month from the days it holds; None when it holds none. A day with no row adds
    nothing: it was never counted (BR4)."""
    held = [
        (day, days[day])
        for day in (month + timedelta(days=offset) for offset in range(days_in_month(month)))
        if day in days
    ]
    if not held:
        return None
    summed = _summed(held, first_event_at=first_event_at, now=now, zone=zone)
    today = now.astimezone(zone).date()
    month_ended = day_bounds(next_month(month), zone)[0]
    changes = [
        (counts.changed_at, counts.changed_why)
        for _, counts in held
        if counts.changed_at is not None and counts.changed_at >= month_ended
    ]
    changed_at, changed_why = max(changes, key=lambda change: change[0], default=(None, None))
    earlier = [day for day, counts in held if counts.definition_version < definition_version]
    is_check = kind == CHECK_AVAILABILITY
    return MonthFigures(
        month=month,
        result=month_result(
            objective, total=summed.total, failures=summed.failures, coverage=summed.coverage
        ),
        figure_percent=figure_percent(summed.good, summed.total),
        good=summed.good,
        total=summed.total,
        failures=summed.failures,
        coverage=summed.coverage,
        quiet_minutes=sum(counts.quiet_minutes for _, counts in held),
        longest_quiet_minutes=longest_quiet([counts for _, counts in held]),
        lost_minutes=summed.lost_minutes,
        excluded_minutes=summed.excluded_minutes,
        missed_runs=sum(counts.missed_runs or 0 for _, counts in held) if is_check else None,
        maintenance_runs=(
            sum(counts.maintenance_runs or 0 for _, counts in held) if is_check else None
        ),
        days_held=len(held),
        days_in_month=days_in_month(month),
        may_still_change=any((today - day).days < FINAL_AFTER_DAYS for day, _ in held),
        loss_known=all(counts.loss_known for _, counts in held),
        changed_at=changed_at,
        changed_why=changed_why,
        earlier_definition_through=max(earlier, default=None),
        sharp_falls=()
        if is_check
        else sharp_falls(
            days,
            within=[day for day, _ in held],
            partial={today, *([first_event_at.astimezone(zone).date()] if first_event_at else [])},
        ),
    )


def months_of(
    days: Mapping[date, DayCounts],
    *,
    kind: str,
    objective: Decimal,
    definition_version: int,
    first_event_at: datetime | None,
    now: datetime,
    zone: ZoneInfo,
) -> list[MonthFigures]:
    """FR27: the months holding a day, newest first, the current one included, to 13."""
    month = month_start(now.astimezone(zone).date())
    found: list[MonthFigures] = []
    for _ in range(MONTHS_SHOWN):
        figures = month_figures(
            days,
            month=month,
            kind=kind,
            objective=objective,
            definition_version=definition_version,
            first_event_at=first_event_at,
            now=now,
            zone=zone,
        )
        if figures is not None:
            found.append(figures)
        month = previous_month(month)
    return found


def earliest_month_shown(today: date) -> date:
    """The oldest month `months_of` can show from `today`."""
    month = month_start(today)
    for _ in range(MONTHS_SHOWN - 1):
        month = previous_month(month)
    return month


def sharp_falls(
    days: Mapping[date, DayCounts], *, within: Iterable[date], partial: Iterable[date]
) -> tuple[SharpFall, ...]:
    """FR27c: each day in `within` whose requests are at least 50% below the median of
    the same weekday in up to four earlier weeks held. A partial day, today's or the
    first event's, is never compared, nor compared against."""
    skipped = set(partial)
    falls: list[SharpFall] = []
    for day in within:
        if day in skipped or day not in days:
            continue
        earlier = [
            days[before].total
            for before in (
                day - timedelta(weeks=weeks) for weeks in range(1, SHARP_FALL_WEEKS_COMPARED + 1)
            )
            if before in days and before not in skipped
        ]
        if not earlier:
            continue
        usual = Decimal(statistics.median(earlier))
        if usual <= 0:
            continue
        fall = (usual - days[day].total) * 100 / usual
        if fall >= SHARP_FALL_PERCENT:
            falls.append(SharpFall(day=day, fall_percent=int(fall)))
    return tuple(falls)


def recount_limit(today: date, *, retention_days: int) -> date:
    """FR28: the oldest day a change may rewrite: the later of what the source still
    holds and the 29 days nexovue's loss records last."""
    held_from = today - timedelta(days=retention_days - 1)
    return max(held_from, today - timedelta(days=LOSS_RECORDS_DAYS))


# FR27b: the fixed label every copied month and CSV row carries, by kind (D42).
_REQUEST_REPORT_LABEL: Final = (
    "Measured from requests that reached the service and were reported. Not a contract figure."
)
_CHECK_REPORT_LABEL: Final = (
    "Measured from nexovue's own check runs, one view from outside. Runs in maintenance "
    "windows are not counted. Not a contract figure."
)
# Founder decision 2 (foundation item 16): until a lawyer reads it, a figure is for
# the team and the board.
AUDIENCE_LINE: Final = "For your own team and board, not for customer promises."


def report_label(kind: str) -> str:
    """The fixed label for a kind's month, used by the copy action and the CSV alike."""
    return _CHECK_REPORT_LABEL if kind == CHECK_AVAILABILITY else _REQUEST_REPORT_LABEL


# ------------------------------------------------------------------------- burn
#
# FR14 to FR16: three fixed tiers, each a long and a short window and a rate. The rate
# arithmetic is here, not in the scheduler's `burn`, because the API's chart plots the
# same rate and must not differ from what judged it (design, *One target*).

# What a burn is about (design, *The link to findings*): its type, for FR21's pairs.
ABOUT_SIGNALS: Final = ("availability", "latency")


class TierName(enum.StrEnum):
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


@dataclass(frozen=True, slots=True)
class Tier:
    """One of FR14's tiers: its windows, its rate by window, and its severity."""

    name: TierName
    long: timedelta
    short: timedelta
    rate_over_28_days: float
    rate_over_7_days: float
    severity: Literal["critical", "warning"]

    def rate(self, window_days: int) -> float:
        return self.rate_over_7_days if window_days == 7 else self.rate_over_28_days


TIERS: Final = (
    Tier(TierName.FAST, timedelta(hours=1), timedelta(minutes=5), 14.4, 16.8, "critical"),
    Tier(TierName.MEDIUM, timedelta(hours=6), timedelta(minutes=30), 6.0, 5.6, "critical"),
    Tier(TierName.SLOW, timedelta(hours=24), timedelta(hours=2), 3.0, 2.8, "warning"),
)
# The longest long window: what a judging reads by the minute, and how long a source
# must be silent before a burn on it closes (FR17).
LONGEST_TIER_WINDOW: Final = max(tier.long for tier in TIERS)
# FR15: a tier's rate is capped at this share of the largest rate possible.
CAPPED_SHARE_OF_LARGEST: Final = 0.9


def tier_named(name: str) -> Tier:
    return next(tier for tier in TIERS if tier.name == name)


def allowed_share(objective: Decimal) -> float:
    """The share of events the objective allows to fail: 0.001 at 99.9%."""
    return float((Decimal(100) - objective) / Decimal(100))


def burn_rate(failures: int, events: int, objective: Decimal) -> float | None:
    """How many times faster than allowed failures arrived (FR14): the failing share over
    the allowed share. None with no events: an empty window is a gap, never 0 (AC109)."""
    if events <= 0:
        return None
    return (failures / events) / allowed_share(objective)


def tier_limit(tier: Tier, objective: Decimal, window_days: int) -> float:
    """The rate a tier must exceed: its own, capped at 90% of the largest possible, so a
    90% target can still page (FR15, AC15)."""
    return min(tier.rate(window_days), CAPPED_SHARE_OF_LARGEST / allowed_share(objective))


def signal_of(kind: str) -> Literal["availability", "latency"]:
    """What a burn on this kind is about, for FR21's pairs. A check's runs are its
    availability."""
    return "latency" if kind == SERVICE_LATENCY else "availability"


# ------------------------------------------------------------------- exclusions
#
# FR29: one-time stretches a person leaves out of one target, with a reason. Each limit
# is a guess, in the spec's register.

EXCLUSION_REASON_SHORTEST: Final = 10
EXCLUSION_REASON_LONGEST: Final = 500
EXCLUSION_LONGEST: Final = timedelta(days=7)
# The most a window may leave out: 7 days of 28, or 2 of 7.
EXCLUDED_MOST_BY_WINDOW: Final = {28: timedelta(days=7), 7: timedelta(days=2)}
# Live exclusions per target whose time lies within the figures still kept.
EXCLUSIONS_MOST: Final = 50
# How long daily figures, and so a removed exclusion, are kept (founder decision 20).
FIGURES_KEPT: Final = timedelta(days=400)


def excluded_minutes_in(
    spans: Iterable[tuple[datetime, datetime]], *, start: datetime, end: datetime
) -> frozenset[datetime]:
    """Every whole minute in [start, end) that an exclusion covers. A minute is left out
    when the exclusion holds its start, so a span is whole minutes on both sides."""
    found: set[datetime] = set()
    one = timedelta(minutes=1)
    for starts_at, ends_at in spans:
        minute = max(start, starts_at.replace(second=0, microsecond=0))
        last = min(end, ends_at)
        while minute < last:
            if minute >= starts_at:
                found.add(minute)
            minute += one
    return frozenset(found)


# The fixed names nexovue gives incidents no rule is filed under (D57, D58), so no person
# typed them and deleting a rule changes nothing about them.
EXCLUSION_NOTICE_RULE_NAME: Final = "Reliability target exclusions"
# D57: the rules the target form writes, named after the target's id, never its name.
_FORM_RULE_ID_LENGTH: Final = 8


def form_rule_name(target_id: object, *, criticals_only: bool) -> str:
    name = f"Reliability target rule {str(target_id)[:_FORM_RULE_ID_LENGTH]}"
    return f"{name}, criticals" if criticals_only else name


BLIND_RULE_NAME: Final = "Reliability targets not being judged"


# --------------------------------------------------------------- the burn line
#
# The target page's chart (design, *One target*; AC109): each minute's long-window rate
# of one tier, from the same per-minute counts the job judges, so the point at the
# job's minute equals the rate it stored.

# A source's minute as (events, failures).
MinuteTally = tuple[int, int]


def without_left_out(
    service: Mapping[datetime, MinuteTally], left_out: Iterable[Mapping[datetime, MinuteTally]]
) -> dict[datetime, MinuteTally]:
    """A service's minutes less its left-out endpoints' (FR34): subtracted only where the
    service has a row, and never below zero, because the service cap applies to service
    rows and not endpoint rows."""
    removed: dict[datetime, list[int]] = {}
    for endpoint in left_out:
        for minute, (requests, failures) in endpoint.items():
            if minute in service:
                totals = removed.setdefault(minute, [0, 0])
                totals[0] += requests
                totals[1] += failures
    counted: dict[datetime, MinuteTally] = {}
    for minute, (requests, failures) in service.items():
        requests_out, failures_out = removed.get(minute, (0, 0))
        kept = max(0, requests - requests_out)
        counted[minute] = (kept, min(kept, max(0, failures - failures_out)))
    return counted


def rolling_rates(
    minutes: Mapping[datetime, MinuteTally],
    *,
    first: datetime,
    last: datetime,
    length: timedelta,
    excluded: frozenset[datetime],
    objective: Decimal,
) -> list[tuple[datetime, float | None]]:
    """For each minute from `first` to `last`, the burn rate over the window of `length`
    ending with it, excluded minutes left out (FR29). None where the window holds no
    event: a gap, never 0 (AC109)."""
    one = timedelta(minutes=1)
    span = int(length / one)
    start = first - one * (span - 1)
    tallies: list[MinuteTally] = []
    minute = start
    while minute <= last:
        tallies.append((0, 0) if minute in excluded else minutes.get(minute, (0, 0)))
        minute += one
    events = failures = 0
    rates: list[tuple[datetime, float | None]] = []
    for index, (count, failed) in enumerate(tallies):
        events += count
        failures += failed
        if index >= span:
            gone_events, gone_failures = tallies[index - span]
            events -= gone_events
            failures -= gone_failures
        if index >= span - 1:
            rates.append((start + one * index, burn_rate(failures, events, objective)))
    return rates
