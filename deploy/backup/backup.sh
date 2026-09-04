#!/usr/bin/env bash
# Takes one snapshot of both stores into a single directory on the host.
#
# Both halves together, because a control plane without its telemetry and
# telemetry without its tenants are each unusable on their own.
#
#   deploy/backup/backup.sh
#
# Writes $NEXOVUE_BACKUP_DIR/<UTC timestamp>/ containing:
#   postgres.dump    pg_dump custom format, zstd
#   clickhouse.zip   BACKUP DATABASE, whole database including the watermarks
#   manifest.txt     what was captured, and the numbers a restore is checked against
#   SHA256SUMS       so corruption is detectable without restoring
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/backup/common.sh
source "$here/common.sh"

BACKUP_DIR="${NEXOVUE_BACKUP_DIR:-$here/../../.backups}"
taken_at="$(date -u +%Y-%m-%dT%H%M%SZ)"
snapshot="$BACKUP_DIR/$taken_at"

require_container "$CLICKHOUSE_CONTAINER"
require_container "$POSTGRES_CONTAINER"

mkdir -p "$snapshot"
echo "snapshot: $snapshot"

# ---------- Postgres ----------
# Custom format rather than plain SQL: it restores selectively, it compresses,
# and pg_restore refuses a truncated file instead of replaying half a database.
# Written through stdout to the host, so nothing depends on the container being
# able to write to a mount.
echo "postgres: dumping"
docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$POSTGRES_CONTAINER" \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --compress=zstd > "$snapshot/postgres.dump"

# ---------- ClickHouse ----------
# The server can only write to the disk backups.xml allows, which is inside its
# own data volume — so stage there, copy to the host, and delete the staged copy.
# A backup that lives in the volume it protects is not a backup.
echo "clickhouse: backing up"
staged="$CLICKHOUSE_BACKUP_STAGE/$taken_at.zip"
docker exec "$CLICKHOUSE_CONTAINER" rm -f "$staged"
ch --query "BACKUP DATABASE $CLICKHOUSE_DB TO Disk('$CLICKHOUSE_BACKUP_DISK', '$taken_at.zip')"
docker cp "$CLICKHOUSE_CONTAINER:$staged" "$snapshot/clickhouse.zip"
docker exec "$CLICKHOUSE_CONTAINER" rm -f "$staged"

# ---------- manifest ----------
# Not decoration. This is what `just backup-verify` compares a restored system
# against; without it "the restore worked" means only that a command exited 0.
echo "manifest: measuring"
{
    echo "# nexovue backup manifest"
    echo "# Row counts are read after the backup completes. They are exact only if"
    echo "# ingest was stopped for the duration; under live ingest a restored table"
    echo "# can legitimately hold fewer rows than the count recorded here."
    echo "taken_at=$taken_at"
    echo "clickhouse_image=$(docker inspect -f '{{.Config.Image}}' "$CLICKHOUSE_CONTAINER")"
    echo "postgres_image=$(docker inspect -f '{{.Config.Image}}' "$POSTGRES_CONTAINER")"
    echo "alembic_revision=$(pg_scalar 'SELECT version_num FROM alembic_version')"
    echo "clickhouse_schema_version=$(ch --query 'SELECT max(version) FROM schema_migrations')"

    # Data-bearing tables only. A materialized view with a TO clause stores
    # nothing of its own, so counting it would count its target table twice.
    ch_tables=$(ch --query "
        SELECT name FROM system.tables
        WHERE database = '$CLICKHOUSE_DB'
          AND engine NOT IN ('MaterializedView', 'View')
          AND engine != 'KeeperMap'
        ORDER BY name FORMAT TSV")
    for table in $ch_tables; do
        echo "ch.$table=$(ch_row_count "$table")"
    done

    # The meter's watermark is recorded below rather than here: it is deliberately
    # not restored, so comparing its row count after a restore would fail by
    # design and teach an operator to ignore this check.
    pg_tables=$(pg_scalar "
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> '$POSTGRES_WATERMARK_TABLE'
        ORDER BY tablename")
    for table in $pg_tables; do
        echo "pg.$table=$(pg_scalar "SELECT count(*) FROM \"$table\"")"
    done

    # Recorded and never restored, in either store. Here so an operator can see
    # what the pipeline had reached, and so a cold-start rebuild has a number to
    # start from — ADR 0068 and ADR 0095.
    for table in "${KEEPERMAP_TABLES[@]}"; do
        echo "watermark.ch.$table=$(ch --query "SELECT count() FROM \`$table\`")"
    done
    echo "watermark.pg.$POSTGRES_WATERMARK_TABLE=$(pg_scalar "SELECT count(*) FROM $POSTGRES_WATERMARK_TABLE")"

    # Which Kafka the watermarks above referred to. A restore into a broker whose
    # end offsets are below these is a restore into a different Kafka, and the
    # watermarks must not come back with the data.
    if docker inspect -f '{{.State.Running}}' "$KAFKA_CONTAINER" 2>/dev/null | grep -q true; then
        for topic in spans-raw logs-raw metrics-raw; do
            kafka_end_offsets "$topic" | sed 's/^/kafka.end./;s/:/=/2'
        done
    fi
} > "$snapshot/manifest.txt"

( cd "$snapshot" && shasum -a 256 postgres.dump clickhouse.zip manifest.txt > SHA256SUMS )

echo
echo "backup complete: $snapshot"
ls -la "$snapshot"
echo
echo "Restore reads this directory:"
echo "  just restore-ch $taken_at"
echo "  just restore-pg $taken_at"
