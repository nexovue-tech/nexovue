"""The settled minute from the real progress table, Kafka metadata and watermarks
(plan T2b.1, C4), against the development stack.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from live import require_stack

from nexovue_scheduler.alerts.sweeps import KafkaWatermarks
from nexovue_scheduler.detection import settle
from nexovue_scheduler.detection.store import client_at

CLICKHOUSE_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_DSN", "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
)
# What the scheduler reads the progress table as: the read-only account.
READER_DSN: Final = os.environ.get(
    "NEXOVUE_CLICKHOUSE_READER_DSN",
    "clickhouse://nexovue_reader:nexovue_reader_dev@localhost:18123/nexovue",
)
KAFKA_BROKERS: Final = os.environ.get("NEXOVUE_KAFKA_BROKERS", "localhost:19092")
KAFKA_TIMEOUT_SECONDS: Final = 10


def _reachable(address: str, default_port: int) -> bool:
    parts = urlsplit(address if "//" in address else f"//{address}")
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port), 2
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(CLICKHOUSE_DSN, 8123) and _reachable(KAFKA_BROKERS, 9092),
    suite="apps/scheduler/tests/test_detection_settle_live.py",
    remedy="ClickHouse or Kafka is not reachable — run `just up`",
)


@pytest.fixture
def live_topic() -> Iterator[str]:
    """A two-partition topic of this test's own, so the worker's real progress is
    neither read nor disturbed. One record in partition 0; partition 1 stays empty."""
    topic = f"detection-settle-test-{uuid.uuid4().hex[:12]}"
    admin = AdminClient({"bootstrap.servers": KAFKA_BROKERS})
    admin.create_topics([NewTopic(topic, num_partitions=2, replication_factor=1)])[topic].result(
        KAFKA_TIMEOUT_SECONDS
    )
    producer = Producer({"bootstrap.servers": KAFKA_BROKERS})
    producer.produce(topic, value=b"span", partition=0)
    assert producer.flush(KAFKA_TIMEOUT_SECONDS) == 0, "the record was not delivered"
    try:
        yield topic
    finally:
        admin.delete_topics([topic])[topic].result(KAFKA_TIMEOUT_SECONDS)
        client_at(CLICKHOUSE_DSN).command(
            "DELETE FROM span_derived_progress WHERE startsWith(partition_key, {prefix:String})",
            parameters={"prefix": f"{topic}:"},
        )


def _record_progress(topic: str, counted_through: int, received_at: datetime) -> None:
    client_at(CLICKHOUSE_DSN).insert(
        "span_derived_progress",
        [(f"{topic}:0", counted_through, received_at)],
        column_names=["partition_key", "counted_through", "counted_through_received_at"],
    )


def test_the_store_and_kafka_settle_a_caught_up_topic_and_hold_a_behind_one(
    live_topic: str,
) -> None:
    progress = settle.connect(READER_DSN)
    kafka = KafkaWatermarks([KAFKA_BROKERS])
    long_ago = datetime.now(UTC) - timedelta(hours=1)

    _record_progress(live_topic, counted_through=-1, received_at=long_ago)
    behind = settle.read_settled(progress, kafka, live_topic)

    _record_progress(live_topic, counted_through=0, received_at=long_ago)
    caught_up = settle.read_settled(progress, kafka, live_topic)

    assert behind.behind and behind.lagging == (0,), "the unread record was not seen"
    assert behind.minute is not None and behind.minute <= long_ago
    assert not caught_up.behind, "an empty partition and a counted one must settle"
    assert abs(caught_up.current - datetime.now(UTC)) < timedelta(minutes=5), (
        "ClickHouse's now came back on another clock"
    )
