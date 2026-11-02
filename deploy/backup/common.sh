#!/usr/bin/env bash
# Shared settings for the backup and restore scripts. Sourced, never run.
#
# Container names rather than a compose file: these run against whatever stack is up,
# and a restore may well be run from a checkout that is not the one that deployed it.
#
# NEXOVUE_PROJECT is the Compose project: `nexovue` for the dev stack and a self-hosted install,
# `nexovue-app` for the replica. `deploy/nexovue backup` sets it and every credential below from the
# install's settings file; the defaults are the dev stack's and nothing else's.

NEXOVUE_PROJECT="${NEXOVUE_PROJECT:-nexovue}"
CLICKHOUSE_CONTAINER="${CLICKHOUSE_CONTAINER:-$NEXOVUE_PROJECT-clickhouse-1}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-$NEXOVUE_PROJECT-postgres-1}"
KAFKA_CONTAINER="${KAFKA_CONTAINER:-$NEXOVUE_PROJECT-kafka-1}"

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

# The four KeeperMap tables holding the ingest pipeline's watermarks, the last
# being how far detection's inputs are counted. Backed up and never restored
# with their rows; the same rule covers the Postgres half below.
KEEPERMAP_TABLES=(span_ingest_offsets span_written_offsets ingest_written_offsets span_derived_progress)

# The Postgres table holding the meter's watermark. Same class of state as the
# four above, in the store that does get restored.
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

# sha256sum on Linux, shasum on a Mac; the same SHA256SUMS format either way.
sha256_write() {
    if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi
}

sha256_check() {
    if command -v sha256sum >/dev/null; then
        sha256sum --check --ignore-missing "$@"
    else
        shasum -a 256 --check --ignore-missing "$@"
    fi
}
