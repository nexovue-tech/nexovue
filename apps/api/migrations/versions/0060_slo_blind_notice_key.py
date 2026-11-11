"""One open "not being judged" notice per project, built without stopping writes

F19 (`docs/features/slo-tracking/`), plan T4.8; follows 0059.

**Why this change is required.** When reliability targets stop being judged, the
delivery sweep opens one incident per project saying so, and resolves it when judging
returns (FR13b, D58). That incident is filed under no rule, so the incident key on
`(rule_id, subject_key)` cannot keep it single: two nulls never conflict. A sweep runs
every 15 seconds, and without its own key each would open another critical incident, or
two at once would. This partial unique index is that key, and the sweep's
`INSERT … ON CONFLICT DO NOTHING` relies on it.

**Affected store and schema.** Postgres: `uq_incidents_blind_open`, unique on
`(project_id, subject_key)` over open incidents with no rule whose subject starts
`slo-project:`. **The `LIKE` is required:** deleting a rule sets `rule_id` null on its
incidents, so without it deleting two rules that each hold an open burn on one target
would fail on this index.

**Locks.** `incidents` holds rows, so the index is built `CONCURRENTLY` in its own
migration, outside a transaction (`docs/engineering/database.md`). A failed build leaves
an invalid index behind, so one of this name is dropped first.

**Compatibility and backfill.** None: no code writes such an incident before this.

**Rollback and recovery.** `downgrade` drops the index concurrently. The sweep would then
open duplicates, so it is not a state to run in.

**Tenant, retention, and security impact.** None: no data is added; every lookup still
names `org_id`.

Revision ID: 0060
Revises: 0059
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BLIND_KEY = "uq_incidents_blind_open"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(BLIND_KEY, "incidents", postgresql_concurrently=True, if_exists=True)
        op.create_index(
            BLIND_KEY,
            "incidents",
            ["project_id", "subject_key"],
            unique=True,
            postgresql_where=sa.text(
                "rule_id IS NULL AND resolved_at IS NULL AND subject_key LIKE 'slo-project:%'"
            ),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(BLIND_KEY, "incidents", postgresql_concurrently=True)
