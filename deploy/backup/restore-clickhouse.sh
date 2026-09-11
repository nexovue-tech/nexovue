#!/usr/bin/env bash
# Restores the telemetry store from a snapshot taken by backup.sh.
#
#   deploy/backup/restore-clickhouse.sh <snapshot>
#
# <snapshot> is a directory name under $NEXOVUE_BACKUP_DIR, or a path.
#
# The database must be empty. The backup carries the schema AND the
# `schema_migrations` row that records which migrations produced it, so running
# `just migrate-ch` first does not help and makes the restore fail.
#
# The three KeeperMap watermark tables are recreated EMPTY, never with their
# rows. A watermark ahead of the broker it names silently skips every
# record below it, and no later flush moves a watermark backwards.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/backup/common.sh
source "$here/common.sh"

BACKUP_DIR="${NEXOVUE_BACKUP_DIR:-$here/../../.backups}"
[ $# -eq 1 ] || die "usage: $(basename "$0") <snapshot>"

snapshot="$1"
[ -d "$snapshot" ] || snapshot="$BACKUP_DIR/$1"
[ -d "$snapshot" ] || die "no such snapshot: $1"
snapshot="$(cd "$snapshot" && pwd)"

require_container "$CLICKHOUSE_CONTAINER"

echo "verifying the artifact before touching the store"
( cd "$snapshot" && shasum -a 256 --check --ignore-missing SHA256SUMS ) \
    || die "checksum mismatch — this snapshot is corrupt, do not restore it"

# Refuse to restore over anything. Restoring onto a populated store is how you
# get a database that is neither the backup nor what was there before.
existing=$(ch --query "SELECT name FROM system.tables WHERE database = '$CLICKHOUSE_DB' FORMAT TSV")
if [ -n "$existing" ]; then
    echo "database '$CLICKHOUSE_DB' is not empty; it holds:" >&2
    echo "$existing" | sed 's/^/  /' >&2
    cat >&2 <<EOF

A restore replaces the whole database and will not merge into an existing one.
If this store is genuinely expendable:

  docker exec -i $CLICKHOUSE_CONTAINER clickhouse-client --user $CLICKHOUSE_USER \\
    --password '***' --query 'DROP DATABASE $CLICKHOUSE_DB SYNC'
  docker exec -i $CLICKHOUSE_CONTAINER clickhouse-client --user $CLICKHOUSE_USER \\
    --password '***' --query 'CREATE DATABASE $CLICKHOUSE_DB'

Dropping the database drops the KeeperMap tables with it. Where this stack's own
Keeper is live and holding ingest watermarks, that discards them — which is what
a restore wants anyway, and is why they are never restored.
EOF
    exit 1
fi

# The server reads only from the disk backups.xml allows, so the artifact has to
# go back into the container before it can be restored.
staged="$CLICKHOUSE_BACKUP_STAGE/restore.zip"
docker exec "$CLICKHOUSE_CONTAINER" mkdir -p "$CLICKHOUSE_BACKUP_STAGE"
docker cp "$snapshot/clickhouse.zip" "$CLICKHOUSE_CONTAINER:$staged"
# `docker cp` writes as root, and the server runs as `clickhouse`: without this
# the RESTORE fails with "Permission denied" on a file that is plainly there.
docker exec -u 0 "$CLICKHOUSE_CONTAINER" chown clickhouse:clickhouse "$staged"
trap 'docker exec "$CLICKHOUSE_CONTAINER" rm -f "$staged" >/dev/null 2>&1 || true' EXIT

except=""
for table in "${KEEPERMAP_TABLES[@]}"; do
    except="${except:+$except, }$CLICKHOUSE_DB.$table"
done

echo "restoring tables and data (excluding the watermarks)"
ch --query "
RESTORE DATABASE $CLICKHOUSE_DB
  EXCEPT TABLES $except
  FROM Disk('$CLICKHOUSE_BACKUP_DISK', 'restore.zip')"

# Structure only: the pipeline needs these tables to exist, and needs them to
# start from "nothing written" rather than from an offset the broker has never
# reached.
echo "recreating the watermark tables empty"
for table in "${KEEPERMAP_TABLES[@]}"; do
    ch --query "
    RESTORE TABLE $CLICKHOUSE_DB.$table
      FROM Disk('$CLICKHOUSE_BACKUP_DISK', 'restore.zip')
      SETTINGS structure_only = true"
done

echo
echo "clickhouse restored from $snapshot"
echo "check it with: just backup-verify $(basename "$snapshot")"
