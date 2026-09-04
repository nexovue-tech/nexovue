#!/usr/bin/env bash
# Shared settings for the backup and restore scripts. Sourced, never run.
#
# Container names rather than a compose file, for the same reason
# deploy/kafka/topics.sh uses them: these run against whatever stack is up, and
# a restore may well be run from a checkout that is not the one that deployed it.

CLICKHOUSE_CONTAINER="${CLICKHOUSE_CONTAINER:-nexovue-clickhouse-1}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-nexovue-postgres-1}"
KAFKA_CONTAINER="${KAFKA_CONTAINER:-nexovue-kafka-1}"

CLICKHOUSE_DB="${CLICKHOUSE_DB:-nexovue}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-nexovue}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-nexovue_dev}"

POSTGRES_DB="${POSTGRES_DB:-nexovue}"
POSTGRES_USER="${POSTGRES_USER:-nexovue}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-nexovue_dev}"

KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka:9092}"

# The staging path inside the ClickHouse container. It is the only disk
# `backups.xml` allows the server to write, and it lives in the data volume, so
# nothing is left here: every artifact is copied to the host and the staged copy
# deleted. See the runbook's "What it costs" for the transient space this needs.
CLICKHOUSE_BACKUP_STAGE="/var/lib/clickhouse/backups"
CLICKHOUSE_BACKUP_DISK="backups"

# The three KeeperMap tables holding the ingest pipeline's watermarks. Backed up
# and never restored with their rows — ADR 0068, and ADR 0095 for the Postgres
# half of the same rule.
KEEPERMAP_TABLES=(span_ingest_offsets span_written_offsets ingest_written_offsets)

# The Postgres table holding the meter's watermark. Same class of state as the
# three above, in the store that does get restored (ADR 0095).
POSTGRES_WATERMARK_TABLE="usage_ingest_offsets"

# None of these three take stdin, and `docker exec -i` would swallow it. Called
# inside a `while read` loop that cost a caller its whole result set once: the
# loop read one line, the query drank the rest, and the check reported PASS
# having compared one table.
ch() {
    docker exec "$CLICKHOUSE_CONTAINER" clickhouse-client \
        --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" \
        --database "$CLICKHOUSE_DB" "$@"
}

pg() {
    docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$POSTGRES_CONTAINER" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 "$@"
}

# One scalar, no headers, no decoration — the shape every check below wants.
pg_scalar() { pg -tA -c "$1"; }

# One ClickHouse table's row count, measured so that it does not move when
# ClickHouse merges parts.
#
# Engines that collapse rows on merge answer count() differently depending on how
# much merging has happened, so a backup and its restore disagree by however many
# merges landed in between: measured on the first rehearsal, `trace_summaries`
# read 263 before the restore and 262 after, with nothing lost. FINAL applies the
# merge at read time, so both sides answer the same. Only the collapsing engines
# pay for it — `spans` and `logs` are plain MergeTree and are counted directly.
ch_row_count() {
    local table="$1" engine
    engine=$(ch --query "
        SELECT engine FROM system.tables
        WHERE database = '$CLICKHOUSE_DB' AND name = '$table'")
    case "$engine" in
        Aggregating*|Replacing*|Summing*|Collapsing*|VersionedCollapsing*)
            ch --query "SELECT count() FROM \`$table\` FINAL" ;;
        *)
            ch --query "SELECT count() FROM \`$table\`" ;;
    esac
}

kafka_end_offsets() {
    docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-get-offsets.sh \
        --bootstrap-server "$KAFKA_BOOTSTRAP" --topic "$1"
}

require_container() {
    if ! docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -q true; then
        echo "container not running: $1" >&2
        exit 1
    fi
}

die() { echo "$*" >&2; exit 1; }
