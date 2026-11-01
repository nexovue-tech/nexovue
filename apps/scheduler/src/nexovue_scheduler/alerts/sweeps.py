"""The service conditions, evaluated every minute by beat (ADR-012).

Each sweep records when it finished — or that it skipped, and why — where the
API can read it, so the Alerts screen can say evaluation is behind rather than
showing a quiet list that means nothing (spec, *Error cases*).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from confluent_kafka import Consumer, TopicPartition
from nexovue_platform.alerts.tasks import (
    DELIVER_TASK,
    DELIVERIES_QUEUE,
    EVALUATE_NEW_ERRORS_TASK,
    EVALUATE_SILENCE_TASK,
    EVALUATE_THRESHOLDS_TASK,
)
from nexovue_platform.jobs import JobRequest
from nexovue_platform.telemetry.logging import get_logger

from nexovue_scheduler.alerts import new_errors, silence, thresholds
from nexovue_scheduler.alerts.telemetry import TelemetryStore, connect
from nexovue_scheduler.jobs import results
from nexovue_scheduler.jobs.registry import SoftTimeLimit, task
from nexovue_scheduler.process_engine import enqueue, with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

KAFKA_TIMEOUT_SECONDS: Final = 5.0
_MILLISECONDS: Final = 1000

NO_TELEMETRY_STORE: Final = "CLICKHOUSE_DSN is not set for the scheduler"
METER_BEHIND: Final = (
    "the usage meter is behind, so a silent service cannot be told from a late one"
)

_telemetry_store: TelemetryStore | None = None
_watermarks: KafkaWatermarks | None = None


class KafkaWatermarks:
    """The newest offset in each partition, asked of Kafka directly. Joins no group
    and commits nothing: it only reads the partitions' high watermarks and the
    topic's metadata."""

    def __init__(self, brokers: list[str]) -> None:
        self._consumer = Consumer(
            {
                "bootstrap.servers": ",".join(brokers),
                "group.id": "nexovue-alerts-meter-probe",
                "enable.auto.commit": False,
            }
        )

    def newest_offset(self, topic: str, partition: int) -> int:
        _, high = self._consumer.get_watermark_offsets(
            TopicPartition(topic, partition), timeout=KAFKA_TIMEOUT_SECONDS, cached=False
        )
        return int(high) - 1

    def offsets_at(self, topic: str, partitions: int, at: datetime) -> dict[int, int]:
        """Each partition's first offset whose record was received at or after `at`,
        by the record's own timestamp; -1 where no record was (F6, the install-wide
        guard's reading of what the receiver accepted)."""
        asked = [
            TopicPartition(topic, partition, int(at.timestamp() * _MILLISECONDS))
            for partition in range(partitions)
        ]
        found = self._consumer.offsets_for_times(asked, timeout=KAFKA_TIMEOUT_SECONDS)
        for each in found:
            if each.error is not None:
                raise KafkaMetadataError(f"no offset at {at} for {topic}:{each.partition}")
        return {each.partition: int(each.offset) for each in found}

    def partition_count(self, topic: str) -> int:
        """How many partitions the topic has, from the cluster's metadata (C4).

        Asked of Kafka, not of stored rows: a partition no worker has written for
        must still count."""
        metadata = self._consumer.list_topics(topic, timeout=KAFKA_TIMEOUT_SECONDS)
        found = metadata.topics.get(topic)
        if found is None or found.error is not None or not found.partitions:
            reason = found.error if found is not None else "absent from the metadata"
            raise KafkaMetadataError(f"topic {topic!r} has no partitions: {reason}")
        return len(found.partitions)


class KafkaMetadataError(RuntimeError):
    """Kafka could not say how many partitions a topic has."""


@task(EVALUATE_THRESHOLDS_TASK)
def evaluate_thresholds() -> None:
    store = _telemetry()
    if store is None:
        _record("thresholds", skipped=NO_TELEMETRY_STORE)
        return
    now = datetime.now(UTC)
    _after(
        "thresholds",
        with_engine(lambda engine: thresholds.evaluate(engine, store, now=now)),
    )


@task(EVALUATE_NEW_ERRORS_TASK)
def evaluate_new_errors() -> None:
    store = _telemetry()
    if store is None:
        _record("new_errors", skipped=NO_TELEMETRY_STORE)
        return
    now = datetime.now(UTC)
    _after(
        "new_errors",
        with_engine(lambda engine: new_errors.evaluate(engine, store, now=now)),
    )


@task(EVALUATE_SILENCE_TASK)
def evaluate_silence() -> None:
    now = datetime.now(UTC)
    progress = with_engine(silence.meter_progress)
    try:
        behind = silence.meter_is_behind(progress, _kafka(), now=now)
    except (SoftTimeLimit, KeyboardInterrupt):
        raise
    except Exception:
        # Kafka could not be asked: not knowing whether the meter is behind is not
        # knowing whether a service is silent.
        logger.exception("could not read the meter's backlog; skipping silence this minute")
        _record("silence", skipped=METER_BEHIND)
        return
    if behind:
        logger.error("usage meter stalled with data waiting; silence not judged this minute")
        _record("silence", skipped=METER_BEHIND)
        return
    _after("silence", with_engine(lambda engine: silence.evaluate(engine, now=now)))


def _after(evaluation: str, written: int) -> None:
    _record(evaluation)
    if written:
        logger.info(
            "alert evaluation wrote notifications", evaluation=evaluation, notifications=written
        )
        enqueue(JobRequest(task=DELIVER_TASK, queue=DELIVERIES_QUEUE))


def _record(evaluation: str, *, skipped: str | None = None) -> None:
    with_engine(lambda engine: results.record_evaluation(engine, evaluation, skipped=skipped))


def _telemetry() -> TelemetryStore | None:
    global _telemetry_store
    if _telemetry_store is None:
        dsn = get_settings().clickhouse_dsn
        if not dsn:
            return None
        _telemetry_store = connect(dsn)
    return _telemetry_store


def _kafka() -> KafkaWatermarks:
    global _watermarks
    if _watermarks is None:
        _watermarks = KafkaWatermarks(get_settings().kafka_brokers)
    return _watermarks
