#!/usr/bin/env bash
# Restores the control plane from a snapshot taken by backup.sh.
#
#   deploy/backup/restore-postgres.sh <snapshot>
#
# <snapshot> is a directory name under $NEXOVUE_BACKUP_DIR, or a path.
#
# The dump carries `alembic_version`, so the schema comes back at the revision it
# was taken at. Do not run `just migrate-pg` first.
#
# `usage_ingest_offsets` is emptied after the restore. It is the meter's Kafka
# watermark and it is the same class of state as ClickHouse's KeeperMap tables,
# which the ClickHouse restore already refuses to bring back; the same rule
# applies to this store. Restored, it would sit above a rebuilt broker's offsets
# and the meter would silently never count the records below it —
# `counted_through` only ever moves forward.
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

require_container "$POSTGRES_CONTAINER"

echo "verifying the artifact before touching the store"
( cd "$snapshot" && shasum -a 256 --check --ignore-missing SHA256SUMS ) \
    || die "checksum mismatch — this snapshot is corrupt, do not restore it"

# One transaction, so a failure leaves the control plane as it was rather than
# half replaced. --clean --if-exists lets the same command restore over a
# populated database and over an empty one.
echo "restoring the control plane"
docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$POSTGRES_CONTAINER" \
    pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --clean --if-exists --no-owner --no-privileges --single-transaction \
    < "$snapshot/postgres.dump"

discarded=$(pg_scalar "SELECT count(*) FROM $POSTGRES_WATERMARK_TABLE")
echo "the meter's watermark as restored, before it is discarded:"
pg -c "SELECT topic, partition_id, counted_through FROM $POSTGRES_WATERMARK_TABLE ORDER BY topic, partition_id"
pg -c "TRUNCATE $POSTGRES_WATERMARK_TABLE" > /dev/null
echo "discarded $discarded watermark row(s)"

echo
echo "postgres restored from $snapshot"
echo "  alembic revision: $(pg_scalar 'SELECT version_num FROM alembic_version')"
echo "check it with: just backup-verify $(basename "$snapshot")"
