"""Reliability target burns: the rule kind, what an incident is about, and exclusions

F19 (`docs/features/slo-tracking/`), plan T4.1 and T4.2; follows F28's 0057, after
F22's 0055 and F20's 0056.

**Why this change is required.** Step 4 alerts when a target's allowed failures run out
too fast. That is an alert rule of a new kind, `slo_burn` (FR14, FR25), so the CHECKs on
`alert_rules` and `incidents` widen. A burn's incident is keyed by its target, not its
service, so `incidents` gains typed columns saying what it is about, for the links and
withholding of Step 5 (design, *The link to findings*). People leave planned work out of
a figure with a reason (FR29), which needs its own table. And every project gets the
default *Reliability targets* rule, as a new one now does (FR25, AC76).

**Affected store and schema.** Postgres:

- `alert_rules.condition_is_known` and `incidents.condition_is_known` admit `slo_burn`.
- `incidents.about_key`, `about_signal`, `about_endpoint` and `about_check_id`, all
  nullable and null for every kind but a burn; `about_signal` is checked against
  `availability` and `latency`.
- `slo_exclusions`: one row per exclusion, keyed to its target by `(org_id, slo_id)` so
  the database refuses one filed under another tenant's target, removed by a mark rather
  than a delete, so its history stays (BR6, BR7). `counted_at` is null while the days it
  touches wait for the scheduler's recount, on its creation and on its removal.
- One `slo_burn` rule per existing live project, to the organization's owners.

**Locks on tables that hold rows.** `alert_rules` and `incidents` hold rows. The widened
CHECKs and the new `about_signal` CHECK are added `NOT VALID`, which holds new rows to
them at once and reads none; 0059 validates them under a lock that lets writes go on
(`docs/engineering/database.md`). Every existing row passes: the widened check admits
everything the old one did, and every existing incident's `about_signal` is null. Adding
nullable columns with no default rewrites nothing. The new table's indexes are built
with it, so need no concurrent build.

**Compatibility and backfill.** The default rule is inserted for every live project of
a live organization, sending to its owners channel, as 0028 did for the first default
(`0028_alerts.py`); it stays silent until a target exists. No other backfill.

**Rollback and recovery.** Roll forward only: `slo_exclusions` holds people's reasons for
leaving time out of a figure, which exist nowhere else, and the rules people may since
have retargeted would go with the kind. Going back past this revision is a restore.

**Tenant, retention, and security impact.** `slo_exclusions` carries `org_id` and
`project_id`, cascades from its target (`organizations/tables.py`), and names people by
id, kept on a member's removal and renamed by the scrub. Its reason is free text a person
typed (BR15): it never enters an incident or a notification (FR33), and the scrub
replaces a removed person's name inside it (AC88). The `about_*` columns hold a service,
endpoint or check id, the names an incident's label already holds. Removed exclusions
lapse at 400 days (`slos/retention.py`).

Revision ID: 0058
Revises: 0057
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.alerts.model` and `nexovue_platform.slo`, held equal
# to them by `test_alert_vocabulary`.
_CONDITION_KINDS = (
    "'check_state', 'new_error_group', 'service_error_rate', "
    "'service_latency_p95', 'service_silent', 'anomaly_finding', 'slo_burn'"
)
_ABOUT_SIGNALS = "'availability', 'latency'"
_SLO_RULE_NAME = "Reliability targets"
_SLO_RULE_CONDITION = '{"kind": "slo_burn", "slo_ids": null, "minimum_severity": "warning"}'
# FR29: a reason of 10 to 500 characters.
_REASON_SHORTEST = 10
_REASON_LONGEST = 500
# Every live project of a live organization gains the rule, to its owners. A module
# constant, so `test_alert_defaults_live.py` runs this very statement (AC76).
BACKFILL_SLO_RULE_SQL = f"""
    WITH created AS (
        INSERT INTO alert_rules
            (org_id, project_id, name, condition_kind, condition, is_default)
        SELECT projects.org_id, projects.id, '{_SLO_RULE_NAME}', 'slo_burn',
               CAST('{_SLO_RULE_CONDITION}' AS jsonb), true
        FROM projects
        JOIN organizations ON organizations.id = projects.org_id
        WHERE projects.deleted_at IS NULL AND organizations.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM alert_rules AS held
              WHERE held.org_id = projects.org_id AND held.project_id = projects.id
                AND held.condition_kind = 'slo_burn' AND held.is_default)
        RETURNING id, org_id
    )
    INSERT INTO alert_rule_channels (org_id, rule_id, channel_id)
    SELECT created.org_id, created.id, notification_channels.id
    FROM created
    JOIN notification_channels
        ON notification_channels.org_id = created.org_id
        AND notification_channels.kind = 'owners'
"""


def upgrade() -> None:
    _set_condition_kinds(_CONDITION_KINDS)
    _incidents_say_what_they_are_about()
    _create_slo_exclusions()
    _backfill_the_default_rule()


def _set_condition_kinds(allowed: str) -> None:
    for table in ("alert_rules", "incidents"):
        op.drop_constraint("condition_is_known", table, type_="check")
        op.create_check_constraint(
            "condition_is_known",
            table,
            f"condition_kind IN ({allowed})",
            postgresql_not_valid=True,
        )


def _incidents_say_what_they_are_about() -> None:
    op.add_column("incidents", sa.Column("about_key", sa.Text(), nullable=True))
    op.add_column("incidents", sa.Column("about_signal", sa.Text(), nullable=True))
    op.add_column("incidents", sa.Column("about_endpoint", sa.Text(), nullable=True))
    # No foreign key: the check named stays a fact of the incident after it is deleted,
    # as `detail.check_id` does.
    op.add_column("incidents", sa.Column("about_check_id", _UUID, nullable=True))
    op.create_check_constraint(
        "about_signal_is_known",
        "incidents",
        f"about_signal IN ({_ABOUT_SIGNALS})",
        postgresql_not_valid=True,
    )


def _create_slo_exclusions() -> None:
    op.create_table(
        "slo_exclusions",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("org_id", _UUID, nullable=False),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("slo_id", _UUID, nullable=False),
        sa.Column("starts_at", _TIMESTAMP, nullable=False),
        sa.Column("ends_at", _TIMESTAMP, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", _UUID, nullable=True),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("removed_at", _TIMESTAMP, nullable=True),
        sa.Column("removed_by", _UUID, nullable=True),
        # When the scheduler last recounted the days this exclusion touches; null while a
        # creation or a removal waits for it (FR29: "queues a recount of the days touched").
        sa.Column("counted_at", _TIMESTAMP, nullable=True),
        # FR29: a removal reaching past the recount limit stays visible as not recounted.
        sa.Column(
            "removal_not_recounted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "slo_id"],
            ["slo_targets.org_id", "slo_targets.id"],
            ondelete="CASCADE",
            name="fk_slo_exclusions_target",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (created_by)",
            name="fk_slo_exclusions_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "removed_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (removed_by)",
            name="fk_slo_exclusions_removed_by",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ends_after_it_starts"),
        sa.CheckConstraint(
            f"length(reason) BETWEEN {_REASON_SHORTEST} AND {_REASON_LONGEST}",
            name="a_reason_is_given",
        ),
        sa.CheckConstraint(
            "removed_at IS NULL OR removed_at >= created_at", name="removed_after_created"
        ),
    )
    # A target's exclusions by time: the counts, the burn and the cap read them so.
    op.create_index(
        "ix_slo_exclusions_org_id_slo_id_starts_at",
        "slo_exclusions",
        ["org_id", "slo_id", "starts_at"],
    )


def _backfill_the_default_rule() -> None:
    """What project creation now does, for what already exists (FR25)."""
    op.execute(BACKFILL_SLO_RULE_SQL)


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: slo_exclusions holds people's reasons for leaving time out of "
        "a figure, found nowhere else. Going back past 0058 is a restore."
    )
