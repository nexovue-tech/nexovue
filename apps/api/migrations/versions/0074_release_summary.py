"""A release summary's notification, and the project's choice of where it goes

F28 (`docs/features/deployment-release-tracking/`), plan Step 5 (T5.6; design section 6,
D52, D53). Its keys are built without stopping writes by 0075.

**Why this change is required.** A project may opt in to one notice per release, sent
to one channel people read, carrying the stored reading's counts and plain words (FR55).
Today a notification names an incident, a finding, or for the project kinds neither,
and a project notice is built from a fixed sentence per kind, so counts have no path
to a message (D52).

- **`notifications.release_period_id`**, nullable, the release period a summary is
  about. Its foreign key cascades, as `incident_id`'s and `finding_id`'s do. Added `NOT
  VALID` and then validated, so the table is never locked against writes while the
  existing rows, all null, are checked.
- **`release_summary`**, a new frozen kind (`_NOTIFICATION_KINDS`, held equal to
  `nexovue_platform` by `test_alert_vocabulary.py`).
- **`names_one_subject`** counts the period beside the incident and the finding, and a
  new **`release_summary_names_its_period`** holds that a summary, and only a summary,
  names one: a summary with no period is refused by the database (AC82).
- **`projects.release_summary_channel_id`**, nullable, the channel the summary goes to.
  Null is off, and off is the default. `ON DELETE SET NULL`: deleting the channel turns
  the summary off (D53, AC82).

**Affected store and schema.** Postgres: two columns, two foreign keys, two checks
replaced and one added. The keys are 0075's.

**Compatibility and backfill.** None needed. Both columns are null on every existing
row, which passes every check.

**Rollback and recovery.** Roll forward only: `release_summary_channel_id` is a person's
choice, found nowhere else, and the summaries sent are the delivery log's record. Going
back past 0074 is a restore.

**Tenant, retention, and security impact.** A summary is deleted with its period, so
with its release and that release's retention (FR40). The channel is checked to be the
project's organization's, and one people read, when it is chosen (`PUT
/projects/{project_id}/release-summary`, under `CONFIGURE_PROJECTS`); the summary's
writer joins it by organization again. Neither column holds free text.

Revision ID: 0074
Revises: 0073
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

# Frozen copies, held equal to `nexovue_platform` by `test_alert_vocabulary.py`: a later
# change to them is a migration of its own.
_NOTIFICATION_KINDS = (
    "'opened', 'escalated', 'resolved', 'repeated', 'flapping', 'closed', 'joined', "
    "'adopted', 'stopped', 'running_again', 'explained', 'release_summary'"
)
_PROJECT_NOTIFICATION_KINDS = "'stopped', 'running_again'"
_RELEASE_SUMMARY = "'release_summary'"

PERIOD_KEY = "fk_notifications_release_period_id_release_periods"
CHANNEL_KEY = "fk_projects_release_summary_channel_id_notification_channels"


def upgrade() -> None:
    op.add_column("notifications", sa.Column("release_period_id", _UUID, nullable=True))
    op.create_foreign_key(
        PERIOD_KEY,
        "notifications",
        "release_periods",
        ["release_period_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.execute(f"ALTER TABLE notifications VALIDATE CONSTRAINT {PERIOD_KEY}")

    _replace_check("kind_is_known", f"kind IN ({_NOTIFICATION_KINDS})")
    _replace_check(
        "names_one_subject",
        "num_nonnulls(incident_id, finding_id, release_period_id) = "
        f"CASE WHEN kind IN ({_PROJECT_NOTIFICATION_KINDS}) THEN 0 ELSE 1 END",
    )
    _add_check(
        "release_summary_names_its_period",
        f"(kind = {_RELEASE_SUMMARY}) = (release_period_id IS NOT NULL)",
    )

    op.add_column("projects", sa.Column("release_summary_channel_id", _UUID, nullable=True))
    op.create_foreign_key(
        CHANNEL_KEY,
        "projects",
        "notification_channels",
        ["release_summary_channel_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.execute(f"ALTER TABLE projects VALIDATE CONSTRAINT {CHANNEL_KEY}")


def _replace_check(name: str, condition: str) -> None:
    op.drop_constraint(name, "notifications", type_="check")
    _add_check(name, condition)


def _add_check(name: str, condition: str) -> None:
    op.create_check_constraint(name, "notifications", condition, postgresql_not_valid=True)
    op.execute(f"ALTER TABLE notifications VALIDATE CONSTRAINT ck_notifications_{name}")


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: projects.release_summary_channel_id is a person's choice, and "
        "the release summaries sent are the delivery log's record, found nowhere else. "
        "Going back past 0074 is a restore."
    )
