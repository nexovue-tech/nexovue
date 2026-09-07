"""The ingest stop valve is one column read by two services, so it is asserted.

`organizations.ingest_suspended_at` is defined here — in the SQLAlchemy model and
in migration 0018 — and read there, by the receiver's Go allowance gate, in a SQL
string no Python import can reach. The Go worker cannot import Python and the
receiver deploys separately, so nothing but a test holds the two spellings
together.

The failure mode is what makes this worth a file of its own, and it is the
quiet kind: rename or drop the column and the receiver's one statement stops
matching, the allowance read errors, and the gate **fails open** — deliberately,
because a broken control plane must not become an ingest outage. So the valve
would stop working and ingest would carry on looking healthy. Nobody finds that
until the day somebody needs to close it.

`test_cross_service_contracts.py` states the general rule this follows:

    Restating a name is how two services drift apart; this is the assertion that
    turns a rename into a failing test instead of an empty chart.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from sqlalchemy import DateTime

from nexovue_api import auth
from nexovue_api.data.postgres.models import Organization

API_SOURCE: Final = Path(auth.__file__).resolve().parents[1]
REPOSITORY_ROOT: Final = API_SOURCE.parents[3]

RECEIVER_ALLOWANCE_QUERY: Final = (
    REPOSITORY_ROOT / "apps" / "receiver" / "internal" / "allowance" / "postgres.go"
)
MIGRATION: Final = (
    REPOSITORY_ROOT
    / "apps"
    / "api"
    / "migrations"
    / "versions"
    / "0018_a_tenant_can_be_stopped_without_being_deleted.py"
)

# The one name both services spell.
VALVE_COLUMN: Final = "ingest_suspended_at"


def test_the_model_defines_the_valve_the_receiver_reads() -> None:
    column = Organization.__table__.columns[VALVE_COLUMN]

    assert isinstance(column.type, DateTime), (
        "the valve is a timestamp, not a flag: the first question about a suspension "
        "is when it started"
    )
    assert column.type.timezone, "an instant without a zone is an instant nobody can compare"


def test_an_untouched_valve_is_open() -> None:
    """NULL is the ordinary state, and nothing may make it anything else.

    A NOT NULL column, or a server default of any value, would mean the day this
    shipped every organization carried a suspension timestamp — and the receiver
    reads any value as closed.
    """
    column = Organization.__table__.columns[VALVE_COLUMN]

    assert column.nullable, "an unset valve must be expressible, and it must be NULL"
    assert column.server_default is None, "a default value here closes the valve on everybody"


def test_the_receiver_reads_the_valve_from_the_organization_row() -> None:
    """Read on the allowance's own row, which is what makes it free to leave on.

    A valve on a table of its own would be a second query on the ingest path,
    and a control that costs a lookup per export is a control nobody turns on.
    """
    statement = RECEIVER_ALLOWANCE_QUERY.read_text(encoding="utf-8")

    assert f"org.{VALVE_COLUMN}" in statement, (
        f"the receiver's allowance statement does not read organizations.{VALVE_COLUMN}; "
        "the stop valve would be a column nothing enforces"
    )
    assert re.search(rf"org\.{VALVE_COLUMN}\s+IS NOT NULL", statement), (
        "the receiver must read the valve as IS NOT NULL: any recorded moment closes it, "
        "so a mistyped future date cannot become a valve that never closes"
    )


def test_the_migration_adds_the_column_the_model_declares() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")

    assert '"organizations"' in migration
    assert f'"{VALVE_COLUMN}"' in migration
    assert "nullable=True" in migration, "NULL is open; the migration must not close the valve"
