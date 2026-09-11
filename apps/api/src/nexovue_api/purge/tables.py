"""Which ClickHouse tables hold a tenant's telemetry, and which deliberately do not.

The inventory is here rather than inferred at run time so that "what a deletion
covers" is reviewable in the source. It is held against the schema twice:
`tests/test_purge_inventory.py` reads the migrations, and
`tests/test_purge_live.py` asks the running server. Either one fails on a table
carrying `tenant_id` that this list omits — so a table added next month breaks
the purge test the day it is created, rather than leaving rows behind the first
time somebody is deleted.
"""

from __future__ import annotations

from typing import Final

# The column every purged table identifies its tenant by. One name across the
# whole store; a table that spelled it differently would be a schema defect,
# and the inventory test is what would catch it.
TENANT_COLUMN: Final = "tenant_id"

# Written by the ingest path from what a customer's SDK sent.
RAW_SIGNAL_TABLES: Final[tuple[str, ...]] = (
    "spans",
    "logs",
    "metrics_sum",
    "metrics_gauge",
    "metrics_histogram",
    "metrics_exp_histogram",
    "metrics_summary",
    "web_vitals",
)

# Derived from the raw signals and stored separately. These are the reason this
# package exists: a mutation on `spans` does not reach a materialized view or a
# rollup the worker writes directly, so every one of them has to be named.
DERIVED_TABLES: Final[tuple[str, ...]] = (
    "span_trace_lookup",
    "trace_summaries",
    "endpoint_stats_1m",
    "service_edges_1m",
    "web_vitals_1m",
)

PURGED_TABLES: Final[tuple[str, ...]] = RAW_SIGNAL_TABLES + DERIVED_TABLES

# Everything else in the database, with the reason it is left alone. Kept as
# data rather than as prose so the inventory test can assert that every table
# on the server is either purged or excluded on purpose — an unclassified table
# is the failure this package exists to prevent.
EXCLUDED_TABLES: Final[dict[str, str]] = {
    "span_trace_lookup_mv": (
        "a materialized view with a TO target, so it stores no rows of its own; "
        "purging span_trace_lookup is what removes the data it wrote"
    ),
    "span_ingest_offsets": (
        "keyed by topic and partition, not by tenant. Losing a watermark makes the "
        "worker re-derive counters it already stored — a silent, permanent over-count "
        ", and an over-bill"
    ),
    "span_written_offsets": (
        "keyed by topic and partition, not by tenant. Losing it replays spans that "
        "were already written (migration 000026)"
    ),
    "ingest_written_offsets": (
        "keyed by topic and partition, not by tenant. Same replay as "
        "span_written_offsets, for the log and metric pipelines (migration 000029)"
    ),
    "schema_migrations": "golang-migrate's own bookkeeping; holds no customer data",
}
