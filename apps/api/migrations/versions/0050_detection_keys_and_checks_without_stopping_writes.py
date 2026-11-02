"""The outbox's finding and project keys, and 0041's checks, without stopping writes

F6 (`docs/features/anomaly-detection/`), follows 0041 (plan task T2c.1).

**Why this change is required.** 0041 lets a notification name a finding, or for
the project kinds no subject, and each subject needs its own unique key so no
retry writes a second *opened* for one finding on one channel, or a second
*stopped* for one project (NFR3). `notifications` holds rows, and a unique key
built in a transaction blocks every write to it for as long as the build takes;
a CHECK added plainly reads every row while holding reads and writes off. 0041
therefore adds its checks `NOT VALID` and builds no key, and this migration does
both without stopping writes (`docs/engineering/database.md`).

**Affected store and schema.** Postgres, in an autocommit block:

- `uq_notifications_finding_id_channel_id_kind_sequence`, a unique constraint. A
  unique constraint cannot be built concurrently, so its index is, and the
  constraint then takes it over (`ADD CONSTRAINT … USING INDEX`), which only
  needs a brief lock. A constraint rather than a bare index, as the incident key
  is (0028) and as the model declares it. The scheduler's
  `ON CONFLICT (finding_id, channel_id, kind, sequence)` infers it by its columns.
- `uq_notifications_project_id_channel_id_kind_sequence_project`, a partial unique
  index over the rows naming no subject; a constraint cannot be partial. The
  project inserts name no conflict target, so any key stops them.
- `VALIDATE CONSTRAINT` on the five checks 0041 added `NOT VALID`. Validating reads
  every row under SHARE UPDATE EXCLUSIVE, which lets reads and writes go on.

No migration from 0042 to 0049 writes a notification, so none relies on the keys
before this one builds them; built after 0043, they are also not rewritten by its
change of `sequence` to `integer`.

**Compatibility and backfill.** None needed. Before this revision no code writes a
notification about a finding or a project, so no duplicate can stand in the keys'
way, and every existing row passes the checks (0041 says why).

**Rollback and recovery.** `downgrade` drops the two keys; the checks stay valid,
since validity is not structure. The partial index is dropped concurrently. The
constraint's index belongs to the constraint, which cannot be dropped
concurrently, so it is dropped with a brief ACCESS EXCLUSIVE lock. A build that
fails, for instance on `lock_timeout` behind a long transaction, leaves an invalid
index behind; the upgrade drops any such leftover first, so running it again when
the table is quiet recovers. A run that built the constraint and failed before
Alembic recorded the revision finds it and leaves it be.

**Tenant, retention, and security impact.** None: no data is added, and every
lookup still names `org_id`.

Revision ID: 0050
Revises: 0049
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINDING_KEY = "uq_notifications_finding_id_channel_id_kind_sequence"
PROJECT_KEY = "uq_notifications_project_id_channel_id_kind_sequence_project"
# 0041's NOT VALID checks, by the names the naming convention gave them.
NOT_VALID_CHECKS = (
    ("projects", "ck_projects_detection_sensitivity_is_known"),
    ("alert_rules", "ck_alert_rules_condition_is_known"),
    ("incidents", "ck_incidents_condition_is_known"),
    ("notifications", "ck_notifications_kind_is_known"),
    ("notifications", "ck_notifications_names_one_subject"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _build_finding_key()
        # An earlier failed build leaves an invalid index of this name, which would make
        # CREATE fail with "already exists" rather than build it again.
        op.drop_index(PROJECT_KEY, "notifications", postgresql_concurrently=True, if_exists=True)
        op.create_index(
            PROJECT_KEY,
            "notifications",
            ["project_id", "channel_id", "kind", "sequence"],
            unique=True,
            postgresql_where=sa.text("incident_id IS NULL AND finding_id IS NULL"),
            postgresql_concurrently=True,
        )
        for table, check in NOT_VALID_CHECKS:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {check}")


def _build_finding_key() -> None:
    # Once the constraint owns the index, the index cannot be dropped as a leftover.
    already_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = 'notifications'::regclass AND conname = :name"
            ),
            {"name": FINDING_KEY},
        )
        .first()
    )
    if already_built:
        return
    op.drop_index(FINDING_KEY, "notifications", postgresql_concurrently=True, if_exists=True)
    op.create_index(
        FINDING_KEY,
        "notifications",
        ["finding_id", "channel_id", "kind", "sequence"],
        unique=True,
        postgresql_concurrently=True,
    )
    op.execute(
        f"ALTER TABLE notifications ADD CONSTRAINT {FINDING_KEY} UNIQUE USING INDEX {FINDING_KEY}"
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(PROJECT_KEY, "notifications", postgresql_concurrently=True)
        op.drop_constraint(FINDING_KEY, "notifications", type_="unique")
