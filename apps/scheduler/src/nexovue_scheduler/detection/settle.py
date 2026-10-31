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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from clickhouse_connect.driver.client import Client
from nexovue_platform.tenant_statements import READ_SETTINGS

from nexovue_scheduler.alerts.thresholds import SETTLING
from nexovue_scheduler.detection.store import client_at

MINUTE: Final = timedelta(minutes=1)
# The same allowance F21's thresholds give a late SDK batch, so the two agree on
# when a minute is complete.
EXPORT_ALLOWANCE: Final = SETTLING

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

    @property
    def behind(self) -> bool:
        return self.minute is None or self.minute < self.current


class TopicOffsets(Protocol):
    """What Kafka says about the span topic (`alerts.sweeps.KafkaWatermarks`)."""

    def partition_count(self, topic: str) -> int: ...

    def newest_offset(self, topic: str, partition: int) -> int: ...


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
    )


def _settled_through(through: datetime) -> datetime:
    """The newest minute M whose end plus the allowance is at or before `through`."""
    return (through - EXPORT_ALLOWANCE - MINUTE).replace(second=0, microsecond=0)


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
