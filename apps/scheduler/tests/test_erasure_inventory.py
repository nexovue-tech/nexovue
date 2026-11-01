"""The erasure inventory as a property of the schema, not of a list somebody kept.

An erasure that misses a table leaves a deleted customer's rows behind, and the
way it would happen is not carelessness — it is a table added six months from
now by somebody who has never read this package. So the list of tables is held
against the migrations here: any `CREATE TABLE` carrying `tenant_id` that
`erasure.tables` does not name fails this suite the day the migration lands.

Three things are held, and they are deliberately independent of each other:

1. every tenant-carrying table in the migrations is either purged or excluded
   with a reason;
2. every purged table has a `DELETE` statement that names it and binds the
   owed tenants;
3. the statement that counts what is left names exactly the same tables. The
   deletions and the proof are written separately on purpose: an omission from
   one is then caught by the other rather than agreeing with it.

Needs nothing running. `test_erasure_live.py` is the one that proves rows move.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from nexovue_scheduler.erasure import statements
from nexovue_scheduler.erasure.tables import EXCLUDED_TABLES, PURGED_TABLES, TENANT_COLUMN

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
CLICKHOUSE_MIGRATIONS: Final = _REPOSITORY_ROOT / "deploy" / "clickhouse" / "migrations"

# A `CREATE TABLE` and its column list, up to the ENGINE clause that closes it.
# Nested parentheses — `LowCardinality(String)`, `CODEC(ZSTD(1))` — are never
# followed by ENGINE, so the non-greedy body stops in the right place.
_CREATE_TABLE: Final = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s*ENGINE",
    re.IGNORECASE | re.DOTALL,
)
_DROP_TABLE: Final = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)", re.IGNORECASE)

# `tenant_id` as a column declaration, with or without backticks. Matching the
# bare word would also hit it inside a comment or a sort key.
_DECLARES_TENANT_COLUMN: Final = re.compile(rf"^\s*`?{TENANT_COLUMN}`?\s+\w", re.MULTILINE)

# Which table each arm of the count statement reads, and the predicate it reads
# it under — parsed rather than trusted, because a copy-paste that left one arm
# pointing at the previous table would otherwise count the same rows twice.
_COUNTED_TABLE: Final = re.compile(
    r"FROM\s+(\w+)\s+WHERE\s+tenant_id\s+IN\s+\{owed_tenants:Array\(String\)\}"
    r"\s+GROUP\s+BY\s+tenant_id",
    re.IGNORECASE,
)
_ALTERED_TABLE: Final = re.compile(r"ALTER\s+TABLE\s+(\w+)\s+DELETE\b", re.IGNORECASE)


def _tables_declaring_a_tenant() -> frozenset[str]:
    """Every table the migrations create with a `tenant_id` column, less the dropped."""
    created: dict[str, bool] = {}
    dropped: set[str] = set()
    for migration in sorted(CLICKHOUSE_MIGRATIONS.glob("*.up.sql")):
        text = migration.read_text(encoding="utf-8")
        for name, columns in _CREATE_TABLE.findall(text):
            created[name.lower()] = bool(_DECLARES_TENANT_COLUMN.search(columns))
        dropped.update(name.lower() for name in _DROP_TABLE.findall(text))
    return frozenset(name for name, has_tenant in created.items() if has_tenant) - dropped


TENANT_TABLES_IN_MIGRATIONS: Final = _tables_declaring_a_tenant()


def test_the_migrations_this_check_reads_are_the_ones_that_ship() -> None:
    """A moved migration directory must break this, not silently guard nothing."""
    assert CLICKHOUSE_MIGRATIONS.is_dir(), f"no ClickHouse migrations at {CLICKHOUSE_MIGRATIONS}"
    assert "spans" in TENANT_TABLES_IN_MIGRATIONS
    assert "web_vitals_1m" in TENANT_TABLES_IN_MIGRATIONS


def test_every_tenant_table_in_the_schema_is_purged() -> None:
    """The whole point: a table added later must not be missed."""
    missed = sorted(TENANT_TABLES_IN_MIGRATIONS - set(PURGED_TABLES))

    assert not missed, (
        f"these tables carry {TENANT_COLUMN} and no purge statement deletes from them, "
        f"so a deleted organization's rows would stay: {missed}"
    )


def test_the_purge_names_no_table_the_schema_does_not_have() -> None:
    """A statement against a dropped table fails the purge for every tenant."""
    unknown = sorted(set(PURGED_TABLES) - TENANT_TABLES_IN_MIGRATIONS)

    assert not unknown, f"purged tables that no migration creates with a tenant: {unknown}"


def test_the_offset_tables_are_excluded_with_their_reason() -> None:
    """The four watermark tables must never be purged by tenant.

    They are keyed by topic and partition. Deleting rows from them makes the
    worker re-derive counters it has already stored — a silent, permanent
    over-count and an over-bill. Named here so removing the
    exclusion is a test failure rather than a quiet change.
    """
    watermarks = (
        "span_ingest_offsets",
        "span_written_offsets",
        "ingest_written_offsets",
        "span_derived_progress",
    )
    for table in watermarks:
        assert table not in PURGED_TABLES, f"{table} must not be purged by tenant"
        assert EXCLUDED_TABLES.get(table), f"{table} is excluded without a recorded reason"


def test_no_table_is_both_purged_and_excluded() -> None:
    overlap = sorted(set(PURGED_TABLES) & set(EXCLUDED_TABLES))

    assert not overlap, f"a table cannot be both purged and left alone: {overlap}"


def test_every_purged_table_has_a_delete_statement_that_names_it() -> None:
    assert set(statements.DELETE_OWED_ROWS) == set(PURGED_TABLES), (
        "the delete statements and the purged tables disagree: "
        f"{sorted(set(statements.DELETE_OWED_ROWS) ^ set(PURGED_TABLES))}"
    )

    mismatched = [
        f"{table} -> {_ALTERED_TABLE.findall(statement)}"
        for table, statement in statements.DELETE_OWED_ROWS.items()
        if _ALTERED_TABLE.findall(statement) != [table]
    ]

    assert not mismatched, f"a delete statement alters a table other than its key: {mismatched}"


def test_every_delete_statement_binds_the_owed_tenants() -> None:
    """Without the predicate, `ALTER TABLE spans DELETE` empties the store."""
    unscoped = [
        table
        for table, statement in statements.DELETE_OWED_ROWS.items()
        if not statements.OWED_TENANTS_PREDICATE.search(statement)
    ]

    assert not unscoped, (
        f"these deletions do not bind {statements.OWED_TENANTS_BINDING} and would "
        f"delete every tenant's rows: {unscoped}"
    )


def test_the_remaining_rows_count_covers_exactly_the_purged_tables() -> None:
    """The proof and the deletion are written separately, and must still agree."""
    counted = _COUNTED_TABLE.findall(statements.COUNT_OWED_ROWS)

    assert len(counted) == len(set(counted)), f"a table is counted twice: {sorted(counted)}"
    assert set(counted) == set(PURGED_TABLES), (
        "the completion check and the purge disagree about which tables hold the "
        f"tenant: {sorted(set(counted) ^ set(PURGED_TABLES))}"
    )


def test_every_counted_table_is_labelled_with_its_own_name() -> None:
    """The label is what a caller reads back; a wrong one misreports the table."""
    labelled = re.findall(
        r"SELECT\s+'(\w+)'(?:\s+AS\s+\w+)?\s*,\s*tenant_id\s*,\s*count\(\)"
        r"(?:\s+AS\s+\w+)?\s*FROM\s+(\w+)\s",
        statements.COUNT_OWED_ROWS,
    )

    assert len(labelled) == len(PURGED_TABLES), (
        f"parsed {len(labelled)} labelled arms, expected {len(PURGED_TABLES)}"
    )
    wrong = [(label, table) for label, table in labelled if label != table]
    assert not wrong, f"an arm reports a table name it does not read: {wrong}"
