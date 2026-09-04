#!/usr/bin/env bash
# Checks a running stack against a snapshot's manifest.
#
#   deploy/backup/verify.sh <snapshot>
#
# Exit 0 means every table came back with the rows the backup recorded, the
# schema is at the revision it was taken at, and no ingest watermark sits above
# the broker offset it names. Anything else is a failed restore, whatever the
# restore commands reported.
#
# The watermark check is the one that catches a restore that looks fine and
# quietly loses data: a watermark above its partition's end offset means every
# record below it is skipped for ever, because no flush moves one backwards.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/backup/common.sh
source "$here/common.sh"

BACKUP_DIR="${NEXOVUE_BACKUP_DIR:-$here/../../.backups}"
[ $# -eq 1 ] || die "usage: $(basename "$0") <snapshot>"

snapshot="$1"
[ -d "$snapshot" ] || snapshot="$BACKUP_DIR/$1"
[ -d "$snapshot" ] || die "no such snapshot: $1"
manifest="$snapshot/manifest.txt"
[ -f "$manifest" ] || die "no manifest in $snapshot"

require_container "$CLICKHOUSE_CONTAINER"
require_container "$POSTGRES_CONTAINER"

failures=0
report() {
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        printf '  ok    %-40s %s\n' "$label" "$actual"
    else
        printf '  FAIL  %-40s expected %s, got %s\n' "$label" "$expected" "$actual"
        failures=$((failures + 1))
    fi
}

value_of() { grep -m1 "^$1=" "$manifest" | cut -d= -f2-; }

echo "checking $snapshot"
echo
echo "schema"
report "alembic_revision" "$(value_of alembic_revision)" \
    "$(pg_scalar 'SELECT version_num FROM alembic_version')"
report "clickhouse_schema_version" "$(value_of clickhouse_schema_version)" \
    "$(ch --query 'SELECT max(version) FROM schema_migrations')"

echo
echo "clickhouse rows"
while IFS='=' read -r key expected; do
    table="${key#ch.}"
    report "$table" "$expected" "$(ch_row_count "$table")"
done < <(grep '^ch\.' "$manifest")

echo
echo "postgres rows"
while IFS='=' read -r key expected; do
    table="${key#pg.}"
    report "$table" "$expected" "$(pg_scalar "SELECT count(*) FROM \"$table\"")"
done < <(grep '^pg\.' "$manifest")

echo
echo "ingest watermarks — never restored, so these are the live pipeline's own"
for table in "${KEEPERMAP_TABLES[@]}"; do
    printf '  %-28s %s row(s) live, %s in the backup\n' "$table" \
        "$(ch --query "SELECT count() FROM \`$table\`")" \
        "$(value_of "watermark.ch.$table")"
done
printf '  %-28s %s row(s) live, %s in the backup\n' "$POSTGRES_WATERMARK_TABLE" \
    "$(pg_scalar "SELECT count(*) FROM $POSTGRES_WATERMARK_TABLE")" \
    "$(value_of "watermark.pg.$POSTGRES_WATERMARK_TABLE")"

echo
echo "no watermark above its broker offset"
if docker inspect -f '{{.State.Running}}' "$KAFKA_CONTAINER" 2>/dev/null | grep -q true; then
    # One line per live watermark, as `topic:partition<TAB>offset`, from all four
    # tables at once — the two stores hold the same shape of state and the same
    # rule applies to both. The offset column differs per table, so each is named
    # rather than guessed: `span_ingest_offsets` is the counter guard and carries
    # a range, the other three carry a single "written through".
    live_watermarks() {
        ch --query "SELECT partition_key, max_offset FROM span_ingest_offsets FORMAT TSV"
        for table in span_written_offsets ingest_written_offsets; do
            ch --query "SELECT partition_key, written_through FROM \`$table\` FORMAT TSV"
        done
        pg_scalar "SELECT topic || ':' || partition_id || E'\t' || counted_through FROM $POSTGRES_WATERMARK_TABLE"
    }

    ends=$(for topic in spans-raw logs-raw metrics-raw; do kafka_end_offsets "$topic"; done)
    checked=0
    while IFS=$'\t' read -r partition_key offset; do
        [ -n "$partition_key" ] || continue
        end=$(echo "$ends" | awk -F: -v k="$partition_key" '$1":"$2 == k {print $3}')
        [ -n "$end" ] || continue
        checked=$((checked + 1))
        if [ "$offset" -gt $((end - 1)) ]; then
            echo "  FAIL  $partition_key watermark $offset is above end offset $end"
            failures=$((failures + 1))
        fi
    done < <(live_watermarks)
    echo "  checked $checked watermark(s) against the broker"
else
    echo "  skipped — $KAFKA_CONTAINER is not running"
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "PASS — the running stack matches $(basename "$snapshot")"
else
    echo "FAIL — $failures check(s) did not match $(basename "$snapshot")"
    exit 1
fi
