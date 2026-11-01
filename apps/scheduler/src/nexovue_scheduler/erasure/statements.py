"""The statements that erase every owed tenant at once (ADR-014).

**The one deliberate exception to "one tenant per statement".** Everywhere else
a telemetry statement binds `tenant_id = {tenant_id:String}`. These bind
`tenant_id IN {owed_tenants:Array(String)}`, because one mutation per table for
every deleted organization is the whole point: a mutation rewrites most of its
table, so ten deletions must cost one rewrite, not ten. What keeps the list
honest is where it comes from — `owed.OwedTenants`, made only from organizations
already marked deleted — and `test_tenant_chokepoint_structure.py` allows this
binding in this module alone.

**Why `ALTER TABLE ... DELETE`.** A lightweight `DELETE FROM` only masks rows;
the bytes stay until a merge rewrites the part, and an erasure is not discharged
by hiding. `DROP PARTITION` would take other tenants' rows: the partitions are
by day and retention, not by tenant. The heavy mutation removes these tenants'
rows and only theirs; the bytes leave the disk `old_parts_lifetime` (480 s)
after it completes.

Every statement is a module constant with its table written out, so the set of
tables an erasure touches can be read, and the inventory test can parse it.
"""

from __future__ import annotations

import re
from typing import Final

# The parameter every statement here binds, spelled once for the guards.
OWED_TENANTS_BINDING: Final = "{owed_tenants:Array(String)}"
# The predicate itself, not a mention of the parameter.
OWED_TENANTS_PREDICATE: Final = re.compile(r"\btenant_id\s+IN\s+\{owed_tenants:Array\(String\)\}")

# One per table in `tables.PURGED_TABLES`, in that order.
DELETE_OWED_ROWS: Final[dict[str, str]] = {
    "spans": "ALTER TABLE spans DELETE WHERE tenant_id IN {owed_tenants:Array(String)}",
    "logs": "ALTER TABLE logs DELETE WHERE tenant_id IN {owed_tenants:Array(String)}",
    "metrics_sum": "ALTER TABLE metrics_sum DELETE WHERE tenant_id IN {owed_tenants:Array(String)}",
    "metrics_gauge": (
        "ALTER TABLE metrics_gauge DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "metrics_histogram": (
        "ALTER TABLE metrics_histogram DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "metrics_exp_histogram": (
        "ALTER TABLE metrics_exp_histogram DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "metrics_summary": (
        "ALTER TABLE metrics_summary DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "web_vitals": "ALTER TABLE web_vitals DELETE WHERE tenant_id IN {owed_tenants:Array(String)}",
    "profile_samples": (
        "ALTER TABLE profile_samples DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "span_trace_lookup": (
        "ALTER TABLE span_trace_lookup DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "trace_summaries": (
        "ALTER TABLE trace_summaries DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "endpoint_stats_1m": (
        "ALTER TABLE endpoint_stats_1m DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "service_edges_1m": (
        "ALTER TABLE service_edges_1m DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "web_vitals_1m": (
        "ALTER TABLE web_vitals_1m DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "service_stats_1m": (
        "ALTER TABLE service_stats_1m DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
    "detection_readings": (
        "ALTER TABLE detection_readings DELETE WHERE tenant_id IN {owed_tenants:Array(String)}"
    ),
}

# What is left of each owed tenant, per table: the proof, asked of the data. A
# tenant absent from the result has no rows anywhere. Each arm carries its own
# FROM and predicate, which the inventory test parses.
COUNT_OWED_ROWS: Final = """
SELECT 'spans' AS table, tenant_id, count() AS remaining
FROM spans WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'logs', tenant_id, count()
FROM logs WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'metrics_sum', tenant_id, count()
FROM metrics_sum WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'metrics_gauge', tenant_id, count()
FROM metrics_gauge WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'metrics_histogram', tenant_id, count()
FROM metrics_histogram WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'metrics_exp_histogram', tenant_id, count()
FROM metrics_exp_histogram WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'metrics_summary', tenant_id, count()
FROM metrics_summary WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'web_vitals', tenant_id, count()
FROM web_vitals WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'profile_samples', tenant_id, count()
FROM profile_samples WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'span_trace_lookup', tenant_id, count()
FROM span_trace_lookup WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'trace_summaries', tenant_id, count()
FROM trace_summaries WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'endpoint_stats_1m', tenant_id, count()
FROM endpoint_stats_1m WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'service_edges_1m', tenant_id, count()
FROM service_edges_1m WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'web_vitals_1m', tenant_id, count()
FROM web_vitals_1m WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'service_stats_1m', tenant_id, count()
FROM service_stats_1m WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
UNION ALL SELECT 'detection_readings', tenant_id, count()
FROM detection_readings WHERE tenant_id IN {owed_tenants:Array(String)} GROUP BY tenant_id
"""

# This run's mutation on one table, still in flight. `system.mutations` has no
# tenant column; the command text ClickHouse stores is matched instead.
MUTATIONS_IN_FLIGHT: Final = """
SELECT mutation_id, is_killed, latest_fail_reason
FROM system.mutations
WHERE database = currentDatabase()
  AND table = {table:String}
  AND is_done = 0
  AND position(command, 'tenant_id IN') > 0
"""
