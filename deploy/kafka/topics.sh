#!/usr/bin/env bash
# Creates the ingestion topics. Auto-creation is disabled on the broker so a
# typo in a producer fails loudly instead of silently making a new topic.
#
# Run against the running compose stack:
#   deploy/kafka/topics.sh
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka:9092}"
CONTAINER="${KAFKA_CONTAINER:-nexovue-kafka-1}"
REPLICATION="${KAFKA_REPLICATION_FACTOR:-1}"

# spans-raw is keyed by trace_id and every partition is one assembler's whole
# world, so its partition count is the assembler's maximum parallelism.
# Changing it later re-shuffles trace ownership; pick generously now.
SPAN_PARTITIONS="${KAFKA_SPAN_PARTITIONS:-12}"
OTHER_PARTITIONS="${KAFKA_OTHER_PARTITIONS:-4}"

# 7 days of buffer: long enough to replay a full weekend outage into ClickHouse.
# The worker's trustedPastSkew matches this: nothing legitimately delivered can
# be older than the buffer it was replayed from. Raise this and raise that with
# it, or a replayed outage gets counted at receive time instead of its own
# minutes (apps/workers/internal/pipeline/derive/counters.go).
RETENTION_MS="${KAFKA_RETENTION_MS:-604800000}"

# One record is one trace's envelope, so this must clear the largest envelope a
# legal request can produce: RECEIVER_MAX_DECODED_BODY_BYTES (16 MiB) plus
# envelope overhead. Kafka's 1 MiB default silently refused any trace above it,
# and the sender retried a batch nothing could make smaller. If the receiver's
# body cap changes, this changes with it.
MAX_MESSAGE_BYTES="${KAFKA_MAX_MESSAGE_BYTES:-16781312}"

create_topic() {
    local name="$1" partitions="$2"
    docker exec "$CONTAINER" /opt/kafka/bin/kafka-topics.sh \
        --bootstrap-server "$BOOTSTRAP" \
        --create --if-not-exists \
        --topic "$name" \
        --partitions "$partitions" \
        --replication-factor "$REPLICATION" \
        --config "retention.ms=$RETENTION_MS" \
        --config "max.message.bytes=$MAX_MESSAGE_BYTES" \
        --config compression.type=zstd
    echo "topic ready: $name ($partitions partitions)"
}

create_topic spans-raw "$SPAN_PARTITIONS"
create_topic logs-raw "$OTHER_PARTITIONS"
create_topic metrics-raw "$OTHER_PARTITIONS"

# Poison messages land here rather than blocking a partition (P1-20).
create_topic spans-dlq 1
create_topic logs-dlq 1
create_topic metrics-dlq 1
