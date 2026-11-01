"""findings.services: a project finding names the services it holds

F6 (`docs/features/anomaly-detection/`), plan task T5.3, design *UI structure*, the
finding page's *project finding* state.

**Why this change is required.** A project finding (FR15) is one record for many
services that stopped together, and its page lists them. While it is open, each
service's `detection_state` says it belongs to it; the project job clears that
when it closes. So once closed, nothing recorded which services it was about, and
its page could no longer say. The finding now keeps the list itself, as it keeps
its values after its readings expire (AC40).

**Affected store and schema.** Postgres: one JSONB column on `findings`, an empty
list by default. The project job writes it on a project finding; a service finding
keeps it empty, since it names its one service in `service_name`.

**Compatibility and backfill.** Every existing finding takes an empty list. An open
project finding gains its services at the job's next run; a closed one keeps none,
which is what was known of it.

**Rollback and recovery.** Roll forward only: `downgrade` refuses. Dropping the
column would lose which services each closed project finding was about, which
nothing else records, so going back past this revision is a restore
(`docs/operations/rollback.md`).

**Tenant, retention, and security impact.** None: service names on a row already
scoped by `org_id`, removed with its project. They are telemetry text, shown as
text only (design, *Security*).

Revision ID: 0046
Revises: 0045
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column(
            "services",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Roll forward only (group 9, C6).

    It would drop `findings.services`, the only record of which services a closed
    project finding was about.

    Going back past this revision is a restore of the database, never a
    downgrade — the records it would drop exist nowhere else.
    """
    raise RuntimeError(
        "0046 is roll forward only: its downgrade would drop the services each "
        "project finding was about. Restore a backup instead."
    )
