"""The statements a tenant purge runs.

Every one is a module constant with its table and its tenant predicate written
out in the same literal. That is what makes them reviewable, and for the counts
it is also what `tests/test_tenant_chokepoint_structure.py` parses: a statement
whose table name arrived from a variable is invisible to that check.

**Why `ALTER TABLE ... DELETE` and not the alternatives.** A lightweight
`DELETE FROM` only writes a mask that hides the rows at read time; the bytes
stay on disk until a merge happens to rewrite the part, and an erasure
obligation is not discharged by hiding. `DROP PARTITION` is cheaper still and
wrong: no table partitions by tenant — the keys are `(toDate(received_at),
retention_days)` and `(toYYYYMM(minute), derived_retention_days)` — so a
partition holds every tenant that was active that day, and dropping one deletes
other customers' data. The heavy mutation is the only one of the three that
removes this tenant's rows and only this tenant's rows.

**When the bytes actually leave the disk.** The mutation writes new parts
without the rows and marks the old ones inactive; ClickHouse deletes an inactive
part `old_parts_lifetime` later, 480 seconds by default. So the rows are
unreadable the moment the mutation completes and physically gone about eight
minutes after that. Worth knowing before anyone certifies a deletion to the
minute.
"""

from __future__ import annotations

from typing import Final

# One per table in `tables.PURGED_TABLES`, in that order. Spelled out rather
# than generated so the set of tables a deletion touches can be read.
DELETE_TENANT_ROWS: Final[dict[str, str]] = {
    "spans": "ALTER TABLE spans DELETE WHERE tenant_id = {tenant_id:String}",
    "logs": "ALTER TABLE logs DELETE WHERE tenant_id = {tenant_id:String}",
    "metrics_sum": "ALTER TABLE metrics_sum DELETE WHERE tenant_id = {tenant_id:String}",
    "metrics_gauge": "ALTER TABLE metrics_gauge DELETE WHERE tenant_id = {tenant_id:String}",
    "metrics_histogram": (
        "ALTER TABLE metrics_histogram DELETE WHERE tenant_id = {tenant_id:String}"
    ),
    "metrics_exp_histogram": (
        "ALTER TABLE metrics_exp_histogram DELETE WHERE tenant_id = {tenant_id:String}"
    ),
    "metrics_summary": "ALTER TABLE metrics_summary DELETE WHERE tenant_id = {tenant_id:String}",
    "web_vitals": "ALTER TABLE web_vitals DELETE WHERE tenant_id = {tenant_id:String}",
    "profile_samples": "ALTER TABLE profile_samples DELETE WHERE tenant_id = {tenant_id:String}",
    "span_trace_lookup": (
        "ALTER TABLE span_trace_lookup DELETE WHERE tenant_id = {tenant_id:String}"
    ),
    "trace_summaries": "ALTER TABLE trace_summaries DELETE WHERE tenant_id = {tenant_id:String}",
    "endpoint_stats_1m": (
        "ALTER TABLE endpoint_stats_1m DELETE WHERE tenant_id = {tenant_id:String}"
    ),
    "service_edges_1m": "ALTER TABLE service_edges_1m DELETE WHERE tenant_id = {tenant_id:String}",
    "web_vitals_1m": "ALTER TABLE web_vitals_1m DELETE WHERE tenant_id = {tenant_id:String}",
}

# The deletion obligation itself, asked of the store directly: one statement, so
# "is this tenant gone" is one round trip and one answer rather than thirteen
# that could disagree. Each arm carries its own FROM and its own predicate in
# this literal, which is what the chokepoint structure test reads.
COUNT_REMAINING_ROWS: Final = """
SELECT 'spans' AS table, count() AS remaining
FROM spans WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'logs', count()
FROM logs WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'metrics_sum', count()
FROM metrics_sum WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'metrics_gauge', count()
FROM metrics_gauge WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'metrics_histogram', count()
FROM metrics_histogram WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'metrics_exp_histogram', count()
FROM metrics_exp_histogram WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'metrics_summary', count()
FROM metrics_summary WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'web_vitals', count()
FROM web_vitals WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'profile_samples', count()
FROM profile_samples WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'span_trace_lookup', count()
FROM span_trace_lookup WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'trace_summaries', count()
FROM trace_summaries WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'endpoint_stats_1m', count()
FROM endpoint_stats_1m WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'service_edges_1m', count()
FROM service_edges_1m WHERE tenant_id = {tenant_id:String}
UNION ALL SELECT 'web_vitals_1m', count()
FROM web_vitals_1m WHERE tenant_id = {tenant_id:String}
"""

# Why a purge is not finished, when the counts say it is not. `system.mutations`
# has no tenant column, so this narrows by the predicate ClickHouse stored with
# the mutation — verified against 26.3.20.7, which records the command as
# `(DELETE WHERE tenant_id = '<id>')` with the parameter already substituted.
# `position` rather than LIKE: a LIKE pattern would give `%` and `_` in an
# identifier a meaning nobody intended.
MUTATION_PROGRESS: Final = """
SELECT
    table,
    mutation_id,
    is_done,
    is_killed,
    parts_to_do,
    latest_fail_reason
FROM system.mutations
WHERE database = {database:String}
  AND position(command, {tenant_predicate:String}) > 0
ORDER BY table, mutation_id
"""

# The fragment above is looked for. Kept beside the query it belongs to, because
# it is a claim about how this ClickHouse version formats a stored mutation.
STORED_TENANT_PREDICATE: Final = "tenant_id = '{tenant_id}'"
