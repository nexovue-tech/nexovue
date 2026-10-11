#!/usr/bin/env bash
# Fills `trace_summaries` from the spans already stored.
#
# Migration 000028 creates the table empty, and the pipeline only writes rows
# for traces it assembles from now on. Until this has run, the trace list is
# empty for every trace older than the deployment: the screen shows nothing for
# history that exists in `spans`. This is a deployment step for that migration,
# not a convenience.
#
# Run it after `just migrate-ch`, against the running store, naming the store's
# credential and container — there is no default for the password, so a dev one
# can never reach a deployed store (group 9):
#   CLICKHOUSE_PASSWORD=nexovue_dev deploy/clickhouse/backfill-trace-summaries.sh
#   CLICKHOUSE_CONTAINER=nexovue-replica-clickhouse-1 CLICKHOUSE_PASSWORD=... deploy/...
#
# It is safe to run twice, and safe to run while ingest is live. See RE-RUNNING
# and OPEN TRACES below.
set -euo pipefail

CONTAINER="${CLICKHOUSE_CONTAINER:-nexovue-clickhouse-1}"
DATABASE="${CLICKHOUSE_DATABASE:-nexovue}"
USERNAME="${CLICKHOUSE_USER:-nexovue}"
PASSWORD="${CLICKHOUSE_PASSWORD:?set CLICKHOUSE_PASSWORD to the password of the store you are backfilling}"

# How many spans one statement may aggregate. `trace_summaries` was introduced
# because grouping a whole window by trace id is what exhausts this server:
# the 7-day trace list read 12.48M rows into 292 MiB of aggregation state, and a
# wider probe was killed at 1.71 GiB against a 2.5 GiB cap. So the backfill runs
# in chunks of a size a single group-by is known to survive, and never asks the
# store to do in one statement what the read path was changed to stop doing.
SPANS_PER_STATEMENT="${BACKFILL_SPANS_PER_STATEMENT:-1000000}"

# How settled a trace must be before this script summarises it. A trace whose
# last span reached storage more recently than this may still be held by the
# worker's own accumulator, which will write a more complete row for it — so we
# leave it alone rather than race it. The default is the worker's own bound on
# how long that can last: WORKERS_SAMPLING_WINDOW (10s) plus
# WORKERS_SAMPLING_DECISION_TTL (60s). Past it the worker has forgotten the
# trace, and a later span starts a second row for the worker exactly as it would
# for us.
SETTLE_SECONDS="${BACKFILL_SETTLE_SECONDS:-70}"

# The distinct (service, operation) pairs one trace contributes, matching
# maxTraceEndpoints in apps/workers/internal/pipeline/traceassembly/summary.go.
# Nothing else bounds the set; measured p99 is 19 and the maximum 48.
MAX_TRACE_ENDPOINTS="${BACKFILL_MAX_TRACE_ENDPOINTS:-256}"

# The unit separator joining a service to an operation inside `span_endpoints`,
# also from summary.go. Neither half can contain a control character.
ENDPOINT_SEPARATOR='\x1f'

clickhouse() {
    docker exec -i "$CONTAINER" clickhouse-client \
        --user "$USERNAME" --password "$PASSWORD" --database "$DATABASE" "$@"
}

# WHAT IS DERIVED
#
# Every expression below is the SQL of one field of `traceSummary` in
# apps/workers/internal/pipeline/traceassembly/summary.go. Where the two
# disagree the trace list shows two truths side by side, because a backfilled
# row and a live row for one trace merge into each other.
#
#   start_time      min(timestamp)      earliestStart
#   received_at     min(received_at)    receivedAt — our clock, and the partition
#   duration_ms     latest end minus earliest start, never the root's own span
#   worst_severity  ERROR 2, OK 1, anything else 0, worst wins
#   root_service    the span with no parent; among equals, the earliest start
#   span_services   the distinct services, sorted
#   span_endpoints  the distinct `service \x1f operation` pairs, sorted, capped
#
# Two of them need saying out loud.
#
# `span_count` and `error_count` count DISTINCT span ids, not stored rows,
# because `spans` holds the same span more than once. Measured on the dev store:
# 34.8% of one day's rows were repeats, up to six copies of one span, differing
# only in `retention_days` — a replay after a project's retention changed. The
# worker counts each span once, so counting rows here would report a 10-span
# trace as 60 and disagree with every row the worker writes.
#
# `retention_days` is the earliest-received span's, which is the one the worker
# fixes when it first sees the trace. Ties take the smallest, so the choice is
# reproducible — it is in the partition key, and an irreproducible answer would
# file the same trace in two partitions on two runs — and so that a summary
# expires no later than the spans it points at.
derive_summaries() {
    local day="$1" next_day="$2" day_after="$3" buckets="$4" bucket="$5"

    clickhouse --query "
INSERT INTO trace_summaries
(
    tenant_id, project_id, start_time, trace_id, received_at, retention_days,
    duration_ms, span_count, error_count, worst_severity,
    root_service, root_name, span_services, span_endpoints
)
SELECT
    tenant_id, project_id, earliest_start, trace_id, first_received_at,
    trace_retention_days, trace_duration_ms, span_count, error_count,
    worst_severity, root.1, root.2, span_services, span_endpoints
FROM
(
    -- A root span names no parent. It is written as sixteen NULs, and an SDK
    -- that sends eight zero bytes renders as sixteen ASCII zeroes; both mean
    -- root, so the test strips each and asks whether anything is left.
    WITH notEmpty(replaceAll(replaceAll(toString(parent_span_id), '0', ''), '\0', '')) AS has_parent
    SELECT
        tenant_id,
        project_id,
        min(timestamp)                                        AS earliest_start,
        trace_id,
        min(received_at)                                      AS first_received_at,
        argMin(retention_days, (received_at, retention_days)) AS trace_retention_days,
        (max(toUnixTimestamp64Nano(timestamp) + if(duration_ms > 0, toInt64(duration_ms * 1000000), 0))
           - min(toUnixTimestamp64Nano(timestamp))) / 1000000 AS trace_duration_ms,
        toUInt32(uniqExact(span_id))                          AS span_count,
        toUInt32(uniqExactIf(span_id, status_code = 'ERROR')) AS error_count,
        toUInt8(max(multiIf(status_code = 'ERROR', 2, status_code = 'OK', 1, 0))) AS worst_severity,
        -- One argMin over both halves, so a tie cannot name one span's service
        -- beside another span's operation.
        argMin((service_name, span_name), (has_parent, timestamp)) AS root,
        arraySort(groupUniqArray(toString(service_name)))     AS span_services,
        arraySlice(
            arraySort(groupUniqArray(concat(toString(service_name), '${ENDPOINT_SEPARATOR}', toString(span_name)))),
            1, ${MAX_TRACE_ENDPOINTS})                        AS span_endpoints
    FROM spans
    -- The day this chunk belongs to plus the next one, so a trace whose spans
    -- straddle midnight is still summarised whole. A trace cannot take a day to
    -- arrive: the longest span-arrival spread measured here was 92 minutes.
    WHERE received_at >= toDateTime64('${day} 00:00:00', 9)
      AND received_at <  toDateTime64('${day_after} 00:00:00', 9)
      -- A trace id decides its own chunk, so every chunk holds whole traces.
      AND cityHash64(tenant_id, project_id, trace_id) % ${buckets} = ${bucket}
    GROUP BY tenant_id, project_id, trace_id
    -- Emitted by the day it was first received, so the overlap above cannot
    -- write the same trace twice; and left to the worker while it may still be
    -- held.
    HAVING first_received_at >= toDateTime64('${day} 00:00:00', 9)
       AND first_received_at <  toDateTime64('${next_day} 00:00:00', 9)
       AND max(received_at)  <  now64(9) - toIntervalSecond(${SETTLE_SECONDS})
)
SETTINGS max_bytes_before_external_group_by = 268435456"
}

# RE-RUNNING
#
# Running this twice writes the same rows twice, and that changes nothing the
# reader sees. Every measure on `trace_summaries` merges with max, anyLast or
# set union and deliberately none with sum, and every statement here
# emits a *complete* trace rather than a delta — so a row and its repeat reduce
# to one another, whether a background merge has collapsed them yet or not.
#
# The consequence worth knowing: max means a re-run can only ever raise a value.
# A row already written too high — by an earlier backfill that counted stored
# rows instead of distinct spans, say — is not corrected by running this again.
#
# OPEN TRACES
#
# A trace whose last span landed within SETTLE_SECONDS is skipped, because the
# worker is still holding it and will write a more complete row itself. Skipped
# is not lost: run this again later and it is picked up, or the worker got there
# first and the two rows agree.
main() {
    local days
    days=$(clickhouse --query "SELECT DISTINCT toDate(received_at) FROM spans ORDER BY 1 FORMAT TSV")
    if [ -z "$days" ]; then
        echo "no spans stored: nothing to backfill"
        return
    fi

    local day next_day day_after spans buckets bucket
    for day in $days; do
        next_day=$(clickhouse --query "SELECT toDate('${day}') + 1 FORMAT TSV")
        day_after=$(clickhouse --query "SELECT toDate('${day}') + 2 FORMAT TSV")
        spans=$(clickhouse --query "
            SELECT count() FROM spans
            WHERE received_at >= toDateTime64('${day} 00:00:00', 9)
              AND received_at <  toDateTime64('${day_after} 00:00:00', 9) FORMAT TSV")

        buckets=$(( (spans + SPANS_PER_STATEMENT - 1) / SPANS_PER_STATEMENT ))
        if [ "$buckets" -lt 1 ]; then buckets=1; fi
        echo "${day}: ${spans} spans to read, ${buckets} statement(s)"

        for ((bucket = 0; bucket < buckets; bucket++)); do
            derive_summaries "$day" "$next_day" "$day_after" "$buckets" "$bucket"
        done

        clickhouse --query "
            SELECT '${day}: ' || toString(count()) || ' summary rows in this day''s partitions'
            FROM trace_summaries
            WHERE received_at >= toDateTime64('${day} 00:00:00', 9)
              AND received_at <  toDateTime64('${next_day} 00:00:00', 9) FORMAT TSVRaw"
    done
}

main "$@"
