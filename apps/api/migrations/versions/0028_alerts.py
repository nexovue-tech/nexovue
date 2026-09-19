"""alerts: channels, rules, incidents, and the notifications outbox

F21 (`docs/features/alerts/`). Everything the feature stores for its first four
conditions, in one revision, because none of it is usable alone: a rule with no
channel table has nowhere to send, and an incident with no outbox is a decision
nobody hears about.

**Why this change is required.** nexovue records every check state change and
notifies nobody (spec, *Problem*).

**Affected store and schema.** Postgres only. Seven tables:

- `notification_channels` — the organization's destinations (D4). Plain
  configuration in `config`; a webhook URL that is itself a credential, a routing
  key or a signing secret in `sealed_secret`, sealed with the check-secret key
  under its own purpose (ADR-010, amended). Exactly one `owners` channel per
  organization, enforced by a partial unique index (FR2).
- `alert_rules`, `alert_rule_channels` — a project's rules and where each sends.
  The condition is JSON validated by `nexovue_platform.alerts.model`, which the
  scheduler parses with the same models.
- `alert_evaluations` — a threshold rule's position per subject, so a scheduler
  restart resumes a fire or resolve wait rather than restarting it (Step 5).
- `incidents` — one row per breach. The partial unique index on
  `(rule_id, subject_key) WHERE resolved_at IS NULL` makes a second open incident
  for one breach impossible, whichever path tries (BR1). A deleted rule keeps its
  history: `rule_id` is SET NULL and the rule's name is kept on the row.
- `notifications` — the outbox (ADR-013). Written in the transaction that decided
  it; unique on `(incident_id, channel_id, kind, sequence)`, so no retry can write
  a second *opened* for one incident on one channel (NFR3). Rows delivered
  together share a `batch_id`, which is also the webhook's `webhook-id`.
- `notification_attempts` — the delivery log (FR20), per batch, kept 30 days.

**Compatibility and backfill.** Every live organization gets its owners channel
and every live project its default rule (FR8), exactly as a new signup will —
nexovue is pre-deployment, so this is development data, but the backfill is what
makes the migration correct rather than merely empty.

**Rollback and recovery.** `downgrade` drops the seven tables. Nothing else
references them.

**Tenant, retention, and security impact.** Every table carries `org_id`
(`organizations/tables.py` classifies each for tenant deletion). No secret is
stored in the clear: `sealed_secret` is ciphertext bound to its channel, and
`config` holds only what may be shown.

Revision ID: 0028
Revises: 0027
Created: 2026-09-19
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
_NAME_LENGTH = 120
_LABEL_LENGTH = 64

# Frozen copies of `nexovue_platform.alerts.model`: a migration states what the
# schema was on the day it ran.
_CHANNEL_KINDS = "'owners', 'email', 'slack', 'teams', 'discord', 'pagerduty', 'webhook'"
_CONDITION_KINDS = (
    "'check_state', 'new_error_group', 'service_error_rate', "
    "'service_latency_p95', 'service_silent'"
)
_SEVERITIES = "'critical', 'warning'"
_NOTIFICATION_KINDS = "'opened', 'escalated', 'resolved', 'repeated', 'flapping', 'closed'"
_NOTIFICATION_STATES = "'pending', 'delivering', 'delivered', 'failed', 'dropped'"
_ATTEMPT_OUTCOMES = "'delivered', 'retrying', 'failed'"
_EVALUATION_PHASES = "'ok', 'pending', 'firing', 'resolving', 'insufficient'"

_OWNERS_CHANNEL_NAME = "Organization owners"
_DEFAULT_RULE_NAME = "Any check down"
_DEFAULT_RULE_CONDITION = (
    '{"kind": "check_state", "check_ids": null, "states": {"down": "critical"}}'
)


def _id() -> sa.Column[Any]:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()"))


def _org() -> sa.Column[Any]:
    return sa.Column(
        "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _project() -> sa.Column[Any]:
    return sa.Column(
        "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )


def _timestamp(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        _TIMESTAMP,
        nullable=nullable,
        server_default=sa.text("now()") if now else None,
    )


def _muting() -> list[sa.Column[Any]]:
    """Who muted it and until when (FR14): a mute ends by itself and says whose it is."""
    return [
        _timestamp("muted_until"),
        sa.Column("muted_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        _id(),
        _org(),
        sa.Column("kind", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("name", sa.String(_NAME_LENGTH), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("sealed_secret", sa.LargeBinary(), nullable=True),
        # Days, hours and a time zone; warnings raised inside them are held (FR15).
        sa.Column("quiet_hours", postgresql.JSONB(), nullable=True),
        *_muting(),
        sa.Column(
            "health", sa.String(_LABEL_LENGTH), nullable=False, server_default=sa.text("'healthy'")
        ),
        # The first failure since the last success: an hour of it is *failing* (FR21).
        _timestamp("failing_since"),
        _timestamp("last_delivered_at"),
        # The owners are told once per failing spell, not once per attempt.
        _timestamp("failing_reported_at"),
        _timestamp("created_at", nullable=False, now=True),
        _timestamp("updated_at", nullable=False, now=True),
        sa.CheckConstraint(f"kind IN ({_CHANNEL_KINDS})", name="kind_is_known"),
        sa.CheckConstraint("health IN ('healthy', 'failing')", name="health_is_known"),
    )
    op.create_index("ix_notification_channels_org_id", "notification_channels", ["org_id"])
    op.create_index(
        "uq_notification_channels_org_id_owners",
        "notification_channels",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'owners'"),
    )

    op.create_table(
        "alert_rules",
        _id(),
        _org(),
        _project(),
        sa.Column("name", sa.String(_NAME_LENGTH), nullable=False),
        sa.Column("condition_kind", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("condition", postgresql.JSONB(), nullable=False),
        # Off by default; never more often than every 30 minutes (FR10).
        sa.Column("renotify_minutes", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_muting(),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _timestamp("created_at", nullable=False, now=True),
        _timestamp("updated_at", nullable=False, now=True),
        sa.CheckConstraint(f"condition_kind IN ({_CONDITION_KINDS})", name="condition_is_known"),
        sa.CheckConstraint(
            "renotify_minutes IS NULL OR renotify_minutes >= 30", name="renotify_is_bounded"
        ),
    )
    # The inline path's only question: which enabled rules of this kind does this
    # project have. It runs inside the transaction that records a state change.
    op.create_index(
        "ix_alert_rules_project_id_condition_kind",
        "alert_rules",
        ["project_id", "condition_kind"],
        postgresql_where=sa.text("enabled"),
    )

    op.create_table(
        "alert_rule_channels",
        _org(),
        sa.Column(
            "rule_id",
            _UUID,
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "channel_id",
            _UUID,
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_alert_rule_channels_channel_id", "alert_rule_channels", ["channel_id"])

    op.create_table(
        "alert_evaluations",
        _id(),
        _org(),
        _project(),
        sa.Column(
            "rule_id", _UUID, sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("subject_key", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(_LABEL_LENGTH), nullable=False),
        _timestamp("phase_since", nullable=False),
        sa.Column("last_value", sa.Float(), nullable=True),
        _timestamp("evaluated_at", nullable=False),
        sa.UniqueConstraint(
            "rule_id", "subject_key", name="uq_alert_evaluations_rule_id_subject_key"
        ),
        sa.CheckConstraint(f"phase IN ({_EVALUATION_PHASES})", name="phase_is_known"),
    )

    op.create_table(
        "incidents",
        _id(),
        _org(),
        _project(),
        sa.Column(
            "rule_id", _UUID, sa.ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
        ),
        # Kept so a deleted rule's incidents still say what they were.
        sa.Column("rule_name", sa.String(_NAME_LENGTH), nullable=False),
        sa.Column("condition_kind", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=False),
        # What a message calls the subject: a check's name, a service, an exception.
        sa.Column("subject_label", sa.Text(), nullable=False),
        # The highest reached; it only rises.
        sa.Column("severity", sa.String(_LABEL_LENGTH), nullable=False),
        _timestamp("opened_at", nullable=False),
        _timestamp("resolved_at"),
        # A new error group is an event: opened and closed at once, never resolved.
        sa.Column("is_event", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("flapping", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # The rule was muted when this opened: recorded and shown, never sent (BR5).
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # What fired, for the message and the console: the reading and its threshold,
        # the run and its location. Never a request or response body (BR9).
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint(f"severity IN ({_SEVERITIES})", name="severity_is_known"),
        sa.CheckConstraint(f"condition_kind IN ({_CONDITION_KINDS})", name="condition_is_known"),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= opened_at", name="resolves_after_it_opens"
        ),
    )
    op.create_index(
        "uq_incidents_rule_id_subject_key_open",
        "incidents",
        ["rule_id", "subject_key"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index("ix_incidents_project_id_opened_at", "incidents", ["project_id", "opened_at"])
    # The flap guard counts a subject's recent openings.
    op.create_index(
        "ix_incidents_rule_id_subject_key_opened_at",
        "incidents",
        ["rule_id", "subject_key", "opened_at"],
    )

    op.create_table(
        "notifications",
        _id(),
        _org(),
        _project(),
        sa.Column(
            "incident_id", _UUID, sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "channel_id",
            _UUID,
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(_LABEL_LENGTH), nullable=False),
        # Which repeat this is; zero for everything but `repeated`.
        sa.Column("sequence", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("severity", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column(
            "state", sa.String(_LABEL_LENGTH), nullable=False, server_default=sa.text("'pending'")
        ),
        # Not before this: grouping waits, quiet hours hold, a retry backs off.
        _timestamp("deliver_after", nullable=False, now=True),
        # A claim that ends by itself, so a dead worker's row is reclaimed (ADR-013).
        _timestamp("lease_until"),
        # Rows sent as one message share this; it is also the webhook's `webhook-id`.
        sa.Column("batch_id", _UUID, nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        # Why it was not sent, when it was not: a muted channel, a final refusal.
        sa.Column("outcome_reason", sa.Text(), nullable=True),
        _timestamp("created_at", nullable=False, now=True),
        _timestamp("delivered_at"),
        sa.UniqueConstraint(
            "incident_id",
            "channel_id",
            "kind",
            "sequence",
            name="uq_notifications_incident_id_channel_id_kind_sequence",
        ),
        sa.CheckConstraint(f"kind IN ({_NOTIFICATION_KINDS})", name="kind_is_known"),
        sa.CheckConstraint(f"state IN ({_NOTIFICATION_STATES})", name="state_is_known"),
        sa.CheckConstraint(f"severity IN ({_SEVERITIES})", name="severity_is_known"),
    )
    # The claim: what is due, oldest first, in the two states a claim may take.
    op.create_index(
        "ix_notifications_due",
        "notifications",
        ["deliver_after"],
        postgresql_where=sa.text("state IN ('pending', 'delivering')"),
    )
    op.create_index("ix_notifications_channel_id_state", "notifications", ["channel_id", "state"])
    op.create_index("ix_notifications_batch_id", "notifications", ["batch_id"])

    op.create_table(
        "notification_attempts",
        _id(),
        _org(),
        sa.Column(
            "channel_id",
            _UUID,
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_id", _UUID, nullable=False),
        _timestamp("attempted_at", nullable=False, now=True),
        sa.Column("outcome", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        # In words, for a person (FR20). Never the destination's secret.
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(f"outcome IN ({_ATTEMPT_OUTCOMES})", name="outcome_is_known"),
    )
    op.create_index(
        "ix_notification_attempts_channel_id_attempted_at",
        "notification_attempts",
        ["channel_id", "attempted_at"],
    )
    op.create_index("ix_notification_attempts_batch_id", "notification_attempts", ["batch_id"])
    op.create_index(
        "ix_notification_attempts_attempted_at", "notification_attempts", ["attempted_at"]
    )

    _backfill()


def _backfill() -> None:
    """What signup and project creation now do, for what already exists (FR2, FR8)."""
    op.execute(
        f"""
        INSERT INTO notification_channels (org_id, kind, name)
        SELECT id, 'owners', '{_OWNERS_CHANNEL_NAME}'
        FROM organizations
        WHERE deleted_at IS NULL
        """
    )
    op.execute(
        f"""
        WITH created AS (
            INSERT INTO alert_rules
                (org_id, project_id, name, condition_kind, condition, is_default)
            SELECT projects.org_id, projects.id, '{_DEFAULT_RULE_NAME}', 'check_state',
                   CAST('{_DEFAULT_RULE_CONDITION}' AS jsonb), true
            FROM projects
            JOIN organizations ON organizations.id = projects.org_id
            WHERE projects.deleted_at IS NULL AND organizations.deleted_at IS NULL
            RETURNING id, org_id
        )
        INSERT INTO alert_rule_channels (org_id, rule_id, channel_id)
        SELECT created.org_id, created.id, notification_channels.id
        FROM created
        JOIN notification_channels
            ON notification_channels.org_id = created.org_id
            AND notification_channels.kind = 'owners'
        """
    )


def downgrade() -> None:
    for table in (
        "notification_attempts",
        "notifications",
        "incidents",
        "alert_evaluations",
        "alert_rule_channels",
        "alert_rules",
        "notification_channels",
    ):
        op.drop_table(table)
