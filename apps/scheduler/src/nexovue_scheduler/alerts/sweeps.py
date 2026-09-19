"""The service conditions, evaluated every minute by beat (ADR-012).

Each sweep records when it finished — or that it skipped, and why — where the
API can read it, so the Alerts screen can say evaluation is behind rather than
showing a quiet list that means nothing (spec, *Error cases*).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

from celery.exceptions import SoftTimeLimitExceeded
from confluent_kafka import Consumer, TopicPartition
from nexovue_platform.alerts.tasks import (
    DELIVER_TASK,
    DELIVERIES_QUEUE,
    EVALUATE_NEW_ERRORS_TASK,
    EVALUATE_SILENCE_TASK,
    EVALUATE_THRESHOLDS_TASK,
    evaluation_status_key,
)
from nexovue_platform.telemetry.logging import get_logger
from redis import Redis

from nexovue_scheduler.alerts import new_errors, silence, thresholds
from nexovue_scheduler.alerts.telemetry import TelemetryStore, connect
from nexovue_scheduler.celery_app import app
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)

# A status older than this means the sweep has not run; the API reads it back.
STATUS_TTL_SECONDS: Final = 3600
KAFKA_TIMEOUT_SECONDS: Final = 5.0

NO_TELEMETRY_STORE: Final = "CLICKHOUSE_DSN is not set for the scheduler"
METER_BEHIND: Final = (
    "the usage meter is behind, so a silent service cannot be told from a late one"
)

_telemetry_store: TelemetryStore | None = None
_watermarks: KafkaWatermarks | None = None
_redis: Redis | None = None


class KafkaWatermarks:
    """The newest offset in each partition, asked of Kafka directly. Joins no group
    and commits nothing: it only reads the partitions' high watermarks."""

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


@app.task(name=EVALUATE_THRESHOLDS_TASK, bind=True, max_retries=0)
def evaluate_thresholds(self: Any) -> None:
    store = _telemetry()
    if store is None:
        _record("thresholds", skipped=NO_TELEMETRY_STORE)
        return
    now = datetime.now(UTC)
    _after(
        "thresholds",
        with_engine(lambda engine: thresholds.evaluate(engine, store, now=now)),
    )


@app.task(name=EVALUATE_NEW_ERRORS_TASK, bind=True, max_retries=0)
def evaluate_new_errors(self: Any) -> None:
    store = _telemetry()
    if store is None:
        _record("new_errors", skipped=NO_TELEMETRY_STORE)
        return
    now = datetime.now(UTC)
    _after(
        "new_errors",
        with_engine(lambda engine: new_errors.evaluate(engine, store, now=now)),
    )


@app.task(name=EVALUATE_SILENCE_TASK, bind=True, max_retries=0)
def evaluate_silence(self: Any) -> None:
    now = datetime.now(UTC)
    progress = with_engine(silence.meter_progress)
    try:
        behind = silence.meter_is_behind(progress, _kafka(), now=now)
    except (SoftTimeLimitExceeded, KeyboardInterrupt):
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
        app.send_task(DELIVER_TASK, queue=DELIVERIES_QUEUE)


def _record(evaluation: str, *, skipped: str | None = None) -> None:
    _redis_for_process().set(
        evaluation_status_key(evaluation),
        json.dumps({"finished_at": datetime.now(UTC).isoformat(), "skipped": skipped}),
        ex=STATUS_TTL_SECONDS,
    )


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


def _redis_for_process() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(str(get_settings().redis_url))
    return _redis
