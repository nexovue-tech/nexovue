"""Which minute is settled: every part of the pipeline has finished writing it
(design, "When a minute is settled").

The trace worker records, per partition of the span topic, how far it has
counted into the rollups and when that record was received
(`span_derived_progress`, migration 000037). Each partition is compared with
Kafka's newest offset, as the silence sweep compares the meter's:

- a partition with nothing unread is caught up through now, however long ago it
  last moved: that is a quiet system, not a stuck worker;
- otherwise it is through the `received_at` of its last counted record;
- a partition that holds records but has no progress row is behind, so a worker
  that never started cannot pass for an idle one.

Minute M is settled once every partition is through M + 1 minute + the export
allowance. "Now" is ClickHouse's own clock, read in the same statement as the
progress, so one clock decides.

**Holds** (design revision 2.1, item 1). Two more things keep a minute unsettled
for every project, because both say nexovue's own intake failed rather than a
customer's traffic:

- a minute the receiver recorded refusing exports because the broker would not
  take them (`intake_refusals`, C5);
- the **install-wide guard**: at least 5 organizations lost traffic in the same
  minutes the receiver's accepted count fell. Organizations, not projects, so one
  tenant with many projects is one vote; and the receiver's count must agree, so
  tenants stopping together cannot switch detection off for everyone.

A hold lasts until the pipeline has delivered past its last minute, as any
minute settles, so senders' retries are counted. It never lasts more than 30
minutes from its first: after that its minutes are judged and the operator is
told, so nothing outside nexovue can hold detection for long.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from clickhouse_connect.driver.client import Client
from nexovue_platform.detection import SETTLING_LAG
from nexovue_platform.tenant_statements import READ_SETTINGS

from nexovue_scheduler.detection.lifecycle import PROJECT_ONSET
from nexovue_scheduler.detection.store import client_at

MINUTE: Final = timedelta(minutes=1)
# The allowance a late SDK batch gets past a minute's end: F21's thresholds give the
# same, which `test_detection_settle.py` holds equal.
EXPORT_ALLOWANCE: Final = SETTLING_LAG - MINUTE

# Design 2.1, item 1: a hold lasts at most this long from its first minute.
HOLD_LIMIT: Final = timedelta(minutes=30)
# Held minutes this close are one hold: a minute between them settles no sooner.
HOLD_GAP: Final = SETTLING_LAG
# Far enough back that a hold's real first minute is read: one that began before
# this is past its limit however it is cut.
HOLD_LOOKBACK: Final = 2 * HOLD_LIMIT
# Why a minute is held.
REFUSED: Final = "refused"
GUARDED: Final = "guarded"
# `detection_events.kind`, as operators read it: the guard engaged, and a hold
# released at its limit.
GUARD_EVENT: Final = "intake_guard"
EXPIRED_EVENT: Final = "hold_expired"

# The install-wide guard: this many organizations (design 2.1, item 1).
GUARD_ORGANIZATIONS: Final = 5
# A minute has fallen when the install accepted at most this share of its
# reference rate: half, the normal sensitivity's own minimum change for traffic.
FALL_RATIO: Final = 0.5
# The reference rate is read over this span, just before the hold limit's reach,
# so a fall older than the limit has nothing to fall from.
REFERENCE_SPAN: Final = timedelta(minutes=10)
# Below this many records a minute there is no rate to fall from.
MIN_REFERENCE_RATE: Final = 1.0
# An organization's drop counts toward a fall that began up to this long after it:
# the window's first low minute can come before the install's count shows it.
ONSET_SLACK: Final = PROJECT_ONSET

_NANOSECONDS: Final = 1_000_000_000
_MILLISECONDS: Final = 1_000
_KEY_SEPARATOR: Final = ":"

# Across every tenant on purpose: the progress table describes the pipeline, not
# a customer (the structure test's exemption for this module). The aggregate
# returns one row even when the table is empty, so "now" is always read.
PROGRESS_SQL: Final = """
SELECT
    toUnixTimestamp64Milli(now64(3)) AS now_ms,
    groupArray((
        partition_key,
        counted_through,
        toUnixTimestamp64Nano(counted_through_received_at)
    )) AS marks
FROM span_derived_progress
"""


@dataclass(frozen=True, slots=True)
class Mark:
    """How far one partition is counted into the rollups."""

    counted_through: int
    received_at: datetime


@dataclass(frozen=True, slots=True)
class Settled:
    """The newest settled minute, and why it is not the current one when it is not."""

    # None while a partition holding records has never been counted.
    minute: datetime | None
    # The minute that would be settled were every partition caught up.
    current: datetime
    # Partitions holding records with no progress row.
    missing: tuple[int, ...]
    # Partitions with records unread, held to their last counted record.
    lagging: tuple[int, ...]
    # The clock that decided: ClickHouse's, read with the progress.
    now: datetime

    @property
    def behind(self) -> bool:
        return self.minute is None or self.minute < self.current


class TopicOffsets(Protocol):
    """What Kafka says about the span topic (`alerts.sweeps.KafkaWatermarks`)."""

    def partition_count(self, topic: str) -> int: ...

    def newest_offset(self, topic: str, partition: int) -> int: ...

    def offsets_at(self, topic: str, partitions: int, at: datetime) -> dict[int, int]:
        """Each partition's first offset received at or after `at`; -1 where none was."""
        ...


def settled_minute(
    *,
    now: datetime,
    partition_count: int,
    marks: Mapping[int, Mark],
    newest_offsets: Mapping[int, int],
) -> Settled:
    """The settling rule, with every input already read."""
    through = now
    missing: list[int] = []
    lagging: list[int] = []
    for partition in range(partition_count):
        newest = newest_offsets[partition]
        mark = marks.get(partition)
        if mark is None:
            # A partition that has never held a record has nothing to count.
            if newest >= 0:
                missing.append(partition)
            continue
        if mark.counted_through >= newest:
            continue
        lagging.append(partition)
        through = min(through, mark.received_at)
    return Settled(
        minute=None if missing else _settled_through(through),
        current=_settled_through(now),
        missing=tuple(missing),
        lagging=tuple(lagging),
        now=now,
    )


def _settled_through(through: datetime) -> datetime:
    """The newest minute M whose end plus the allowance is at or before `through`."""
    return (through - SETTLING_LAG).replace(second=0, microsecond=0)


class PipelineProgress:
    """The progress table, read through the raw client this module alone holds for it."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def read(self, topic: str) -> tuple[datetime, dict[int, Mark]]:
        """ClickHouse's now, and every partition of `topic` the worker has recorded."""
        result = self._client.query(PROGRESS_SQL, settings=READ_SETTINGS)
        now_ms, rows = result.result_rows[0]
        marks: dict[int, Mark] = {}
        for key, counted_through, received_ns in rows:
            row_topic, _, partition = str(key).rpartition(_KEY_SEPARATOR)
            if row_topic != topic:
                continue
            marks[int(partition)] = Mark(
                counted_through=int(counted_through),
                received_at=_utc(int(received_ns), _NANOSECONDS),
            )
        return _utc(int(now_ms), _MILLISECONDS), marks


def connect(dsn: str) -> PipelineProgress:
    """The progress table at `clickhouse://user:password@host:port/database`. Reads only."""
    return PipelineProgress(client_at(dsn, settings=READ_SETTINGS))


def read_settled(progress: PipelineProgress, offsets: TopicOffsets, topic: str) -> Settled:
    """The settled minute now.

    Progress is read before Kafka: a record that lands in between is then seen as
    unread, never counted as caught up.
    """
    now, marks = progress.read(topic)
    count = offsets.partition_count(topic)
    newest = {partition: offsets.newest_offset(topic, partition) for partition in range(count)}
    return settled_minute(now=now, partition_count=count, marks=marks, newest_offsets=newest)


def _utc(count: int, per_second: int) -> datetime:
    seconds, remainder = divmod(count, per_second)
    return datetime.fromtimestamp(seconds, UTC) + timedelta(
        microseconds=remainder * 1_000_000 // per_second
    )


# --------------------------------------------------------------------- holds


@dataclass(frozen=True, slots=True)
class Span:
    """A run of minutes, both ends included."""

    first: datetime
    last: datetime

    def meets(self, first: datetime, last: datetime) -> bool:
        return self.first <= last and first <= self.last


@dataclass(frozen=True, slots=True)
class Hold:
    """Minutes unsettled for every project, and why."""

    span: Span
    reason: str


@dataclass(frozen=True, slots=True)
class Held:
    """The settled minute once holds are applied, and the holds that decided it."""

    minute: datetime | None
    holding: tuple[Hold, ...] = ()
    # Holds past HOLD_LIMIT: released, and the operator is to be told.
    expired: tuple[Hold, ...] = ()


def spans(minutes: Iterable[datetime]) -> list[Span]:
    """Minutes chained into spans wherever the next is within `HOLD_GAP`."""
    found: list[Span] = []
    for minute in sorted(set(minutes)):
        if found and minute - found[-1].last <= HOLD_GAP:
            found[-1] = Span(found[-1].first, minute)
        else:
            found.append(Span(minute, minute))
    return found


def refusal_holds(refused: Iterable[datetime]) -> list[Hold]:
    return [Hold(span, REFUSED) for span in spans(refused)]


def apply_holds(settled: Settled, holds: Sequence[Hold], *, now: datetime) -> Held:
    """The settled minute stays before every hold the pipeline has not yet passed,
    unless that hold began more than `HOLD_LIMIT` ago."""
    if settled.minute is None:
        return Held(None)
    holding: list[Hold] = []
    expired: list[Hold] = []
    for hold in holds:
        if hold.span.last <= settled.minute:
            continue
        if now - hold.span.first > HOLD_LIMIT:
            expired.append(hold)
        else:
            holding.append(hold)
    minute = min([settled.minute, *(hold.span.first - MINUTE for hold in holding)])
    return Held(minute, tuple(holding), tuple(expired))


# --------------------------------------------------- the install-wide guard


@dataclass(frozen=True, slots=True)
class Intake:
    """What the receiver got the broker to accept: records a minute over the
    reference span, and in each whole minute since the hold limit's reach."""

    reference: float
    minutes: Mapping[datetime, int]


def fallen(intake: Intake) -> list[datetime]:
    """The minutes the install accepted at most `FALL_RATIO` of its reference rate."""
    if intake.reference < MIN_REFERENCE_RATE:
        return []
    ceiling = FALL_RATIO * intake.reference
    return sorted(minute for minute, count in intake.minutes.items() if count <= ceiling)


def guards(onsets: Mapping[str, datetime], fell: Sequence[datetime]) -> list[Hold]:
    """Each span the install's intake fell in that at least `GUARD_ORGANIZATIONS`
    organizations began losing traffic in. `onsets` is each organization's
    earliest drop. The hold runs from the earliest of those drops."""
    found = []
    for span in spans(fell):
        voters = [
            onset for onset in onsets.values() if span.first - ONSET_SLACK <= onset <= span.last
        ]
        if len(voters) >= GUARD_ORGANIZATIONS:
            found.append(Hold(Span(min(span.first, *voters), span.last), GUARDED))
    return found


def read_intake(offsets: TopicOffsets, topic: str, now: datetime) -> Intake:
    """The span topic's records per minute, read from Kafka by each record's
    receive time: what the receiver accepted, whatever any worker has counted."""
    current = now.replace(second=0, microsecond=0)
    horizon = current - HOLD_LIMIT
    count = offsets.partition_count(topic)
    ends = {partition: offsets.newest_offset(topic, partition) + 1 for partition in range(count)}

    def position(at: datetime) -> int:
        found = offsets.offsets_at(topic, count, at)
        return sum(
            found[partition] if found.get(partition, -1) >= 0 else end
            for partition, end in ends.items()
        )

    boundaries = [horizon + step * MINUTE for step in range(int(HOLD_LIMIT / MINUTE) + 1)]
    positions = [position(at) for at in boundaries]
    reference = (positions[0] - position(horizon - REFERENCE_SPAN)) / (REFERENCE_SPAN / MINUTE)
    return Intake(
        reference=reference,
        minutes={
            boundaries[index]: positions[index + 1] - positions[index]
            for index in range(len(boundaries) - 1)
        },
    )
