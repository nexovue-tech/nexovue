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
# F5 (AC32, ADR-095): tables whose rows a backup never holds, because they are only a
# customer's code or a sealed code: a suggestion's files read, each check's patch, a GitHub
# link attempt. Dumped empty and left out of the manifest. The same list as
# `nexovue_platform.fixes.statements.ROWS_NOT_BACKED_UP`, held equal by a test.
POSTGRES_ROWS_NOT_KEPT=(finding_fix_source_packs finding_fix_checks github_link_attempts)

# Tables a backup holds but cannot show read-only. `CREATE DATABASE ... ENGINE = Backup(...)` attaches
# the MergeTree family and nothing else (measured on 26.3), so `ch_backup_open` restores these into
# a scratch database to count them. `schema_migrations` is TinyLog. A data table of any other engine
# is refused by `backup.sh` until it is named here or given an engine the backup can attach.
CLICKHOUSE_TABLES_NOT_ATTACHABLE=(schema_migrations)

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
#
# `$2` names another database to count in (a backup's, see `ch_backup_open`); the default is the
# live one.
ch_row_count() {
    local table="$1" database="${2:-$CLICKHOUSE_DB}" engine
    engine=$(ch --query "
        SELECT engine FROM system.tables
        WHERE database = '$database' AND name = '$table'")
    if ch_engine_merges_rows "$engine"; then
        ch --query "SELECT count() FROM \`$database\`.\`$table\` FINAL"
    else
        ch --query "SELECT count() FROM \`$database\`.\`$table\`"
    fi
}

ch_engine_merges_rows() {
    case "$1" in
        Aggregating*|Replacing*|Summing*|Collapsing*|VersionedCollapsing*) return 0 ;;
        *) return 1 ;;
    esac
}

# Opens the ClickHouse backup `$1` (a file on the backups disk) for reading, so the manifest can
# count what the archive holds, not what the live tables hold a moment later. Creates two scratch
# databases: `$2`, the tables read straight from the archive (no data is copied), and `$3`, the
# tables that cannot be: CLICKHOUSE_TABLES_NOT_ATTACHABLE, and every table whose engine merges rows,
# because FINAL cannot seek inside a compressed archive (measured on 26.3: CANNOT_SEEK_THROUGH_FILE).
# Those are restored, so they cost their size in disk until `ch_backup_close`, which drops both.
# Prints each table that can be counted, as `database<TAB>table`.
ch_backup_open() {
    local archive="$1" view="$2" extras="$3" table engine attached
    # The caller runs this under `||`, where `set -e` is off: every step says so itself.
    ch --query "DROP DATABASE IF EXISTS \`$view\` SYNC" || return 1
    ch --query "DROP DATABASE IF EXISTS \`$extras\` SYNC" || return 1
    ch --query "CREATE DATABASE \`$view\`
        ENGINE = Backup('$CLICKHOUSE_DB', Disk('$CLICKHOUSE_BACKUP_DISK', '$archive'))" || return 1
    ch --query "CREATE DATABASE \`$extras\`" || return 1
    attached=$(ch --query "
        SELECT name, engine FROM system.tables
        WHERE database = '$view' AND engine NOT IN ('MaterializedView', 'View', 'KeeperMap')
        ORDER BY name FORMAT TSV") || return 1
    while IFS=$'\t' read -r table engine; do
        [ -n "$table" ] || continue
        if ch_engine_merges_rows "$engine"; then
            ch_backup_restore "$archive" "$extras" "$table" || return 1
            printf '%s\t%s\n' "$extras" "$table"
        else
            printf '%s\t%s\n' "$view" "$table"
        fi
    done <<< "$attached"
    for table in "${CLICKHOUSE_TABLES_NOT_ATTACHABLE[@]}"; do
        ch_backup_restore "$archive" "$extras" "$table" || return 1
        printf '%s\t%s\n' "$extras" "$table"
    done
}

ch_backup_restore() {
    ch --query "RESTORE TABLE \`$CLICKHOUSE_DB\`.\`$3\` AS \`$2\`.\`$3\`
        FROM Disk('$CLICKHOUSE_BACKUP_DISK', '$1')" > /dev/null
}

ch_backup_close() {
    ch --query "DROP DATABASE IF EXISTS \`$1\` SYNC" || true
    ch --query "DROP DATABASE IF EXISTS \`$2\` SYNC" || true
}

# What a Postgres dump holds, read from the dump itself: one `rows<TAB>table<TAB>n`
# line per public table it carries data for, sorted, and `first_row<TAB>alembic_version<TAB>rev`.
#
# COPY's text format writes one line per row (a newline inside a value is
# escaped as \n), and every table's data ends with a line holding `\.`. A
# partitioned table's rows are under its partitions, so only those are named.
pg_dump_rows() {
    docker exec -i "$POSTGRES_CONTAINER" pg_restore --data-only --file=- < "$1" | awk '
        BEGIN { FS = "\n" }
        !copying && /^COPY public\.[^ ]+ .* FROM stdin;$/ {
            split($0, words, " ")
            table = substr(words[2], length("public.") + 1)
            gsub(/"/, "", table)
            copying = 1; rows = 0
            next
        }
        copying && $0 == "\\." { print "rows\t" table "\t" rows; copying = 0; next }
        copying {
            rows++
            if (table == "alembic_version" && rows == 1) { print "first_row\talembic_version\t" $0 }
        }
    ' | LC_ALL=C sort
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
