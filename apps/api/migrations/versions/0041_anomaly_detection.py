"""anomaly detection: findings, detection state, and the outbox's other subjects

F6 (`docs/features/anomaly-detection/`), plan task T2c.1, design *Data and
migration impact* as amended by revision 2.1.

**Why this change is required.** Detection judges every service each minute
against its own normal and opens a *finding* when it moves (ADR-022). The
finding, the per-service state the judging resumes from, the hourly normal, and
the controls a person sets all live in Postgres; nothing in the schema holds any
of them today.

**Affected store and schema.** Postgres only. Nine tables:

- `findings` — one row per detected problem (ADR-022). Two partial unique
  indexes make a second open finding for one service, or for one project,
  impossible whichever job tries; two, because Postgres treats nulls as
  distinct. `closed_reason` is set exactly when it is closed. A false alarm is
  its own mark, dated, because one may be put on a finding that already closed
  as recovered (FR20), and an admin's returns its minutes to the normal (D28).
- `finding_incidents` — a finding linked to a threshold or *went silent*
  incident on the same service (BR2).
- `detection_state` — where each service's judging stands: the last judged
  minute, moved by compare-and-set, and each signal's phase (C24: the row is
  created when the service is first seen).
- `detection_project_runs` — when each project's job last completed. Detection
  is *not running* when that is over 3 minutes old (design 2.1, item 2; C17).
  A table rather than a column on `projects`, because it is written every minute
  for every project, and `projects` is the row every request reads. It also
  holds when the owners were told detection stopped, so *running again* is sent
  once (D24).
- `detection_baselines` — the hourly job's normal, one row per service, signal
  and minute it applies to. The minute is absolute (UTC), not a local minute of
  the week: the project job looks up the minute it judges, and the two UTC
  minutes of an autumn fall-back hour each get their own row.
- `baseline_shifts` — a signal's normal moved by a ratio, from a minute, by a
  person (*new normal*) or by adoption (FR21).
- `service_detection_settings` — a service's mute (admins, BR9) and snooze
  (members, D25). Per-service sensitivity is deferred (D32).
- `intake_refusals` and `detection_events` — install-wide, with no `org_id`:
  the receiver's refusals for lack of a store, per minute (C5), and what
  detection did on its own that an operator must see (design 2.1, item 1).

Columns: `projects.timezone` (FR24) and `projects.detection_sensitivity`
(FR18); `notifications.finding_id`. Constraints: `anomaly_finding` joins the
condition kinds of `alert_rules` and `incidents` (FR28), and four kinds join
the notifications': `joined` (D29), `adopted` (D22), and `stopped` and
`running_again` (D24), whose subject is the project.

**The outbox's subjects.** `notifications.incident_id` becomes nullable, and a
check holds every row to exactly one subject: an incident, a finding, or — for
the two project kinds only — neither. Each subject has its own unique key, so no
retry writes a second *opened* for one finding on one channel (NFR3).

**Compatibility and backfill.** Existing projects take the server defaults:
UTC, because no project yet has a creator's zone to copy (C24, C15), and normal
sensitivity. No other backfill: every new table starts empty, and a service's
state row is created when detection first sees it. The widened checks admit
everything they admitted before.

**Rollback and recovery.** `downgrade` drops the nine tables and the two
`projects` columns, and deletes what the narrower schema cannot hold: every
notification with no incident, every row of a new kind, and every rule and
incident of the *anomaly finding* kind. nexovue is pre-deployment; once it is
not, a rollback across this revision is a restore (`docs/operations/rollback.md`).

**Tenant, retention, and security impact.** Every new table but two carries
`org_id`, cascades from its project, and is listed in `organizations/tables.py`
for tenant deletion. `intake_refusals` and `detection_events` name no tenant
and hold no customer data; they are excluded there with that reason. Users
named on a finding or a setting are `SET NULL` when deleted, because deleting
an organization deletes its users before its projects. Findings keep telemetry
text — service and endpoint names in `title` and `signals` — and are purged at
the derived retention (security m4, T6.3); `intake_refusals`,
`detection_events` and `baseline_shifts` are pruned by the expiry sweep (C24).

Revision ID: 0041
Revises: 0040
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
_LABEL_LENGTH = 64
_SERVICE_NAME_LENGTH = 255
_TIMEZONE_NAME_LENGTH = 64
# A receiver's hostname; DNS allows 253 characters.
_RECEIVER_ID_LENGTH = 255

# Frozen copies of `nexovue_platform.alerts.model` and `nexovue_platform.detection`,
# held equal to them by `test_alert_vocabulary`.
_CONDITION_KINDS = (
    "'check_state', 'new_error_group', 'service_error_rate', "
    "'service_latency_p95', 'service_silent', 'anomaly_finding'"
)
_NOTIFICATION_KINDS = (
    "'opened', 'escalated', 'resolved', 'repeated', 'flapping', 'closed', "
    "'joined', 'adopted', 'stopped', 'running_again'"
)
_PROJECT_NOTIFICATION_KINDS = "'stopped', 'running_again'"
_SEVERITIES = "'critical', 'warning'"
_FINDING_SCOPES = "'service', 'project'"
_FINDING_STATES = "'open', 'closed'"
_CLOSED_REASONS = (
    "'recovered', 'false_alarm', 'new_normal', 'adopted', 'service_gone', 'could_not_be_judged'"
)
_SIGNALS = "'traffic', 'failing', 'slower'"
_SHIFT_REASONS = "'new_normal', 'adopted'"
_SENSITIVITIES = "'low', 'normal', 'high'"
_DEFAULT_SENSITIVITY = "normal"
_DEFAULT_TIMEZONE = "UTC"
_INTAKE_REFUSAL_REASONS = "'broker_unavailable'"

# What 0028 allowed, for the downgrade.
_CONDITION_KINDS_BEFORE = (
    "'check_state', 'new_error_group', 'service_error_rate', "
    "'service_latency_p95', 'service_silent'"
)
_NOTIFICATION_KINDS_BEFORE = "'opened', 'escalated', 'resolved', 'repeated', 'flapping', 'closed'"

_NEW_TABLES = (
    "detection_events",
    "intake_refusals",
    "service_detection_settings",
    "baseline_shifts",
    "detection_baselines",
    "detection_project_runs",
    "detection_state",
    "finding_incidents",
    "findings",
)


def _org() -> sa.Column[Any]:
    return sa.Column(
        "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _project(*, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        "project_id",
        _UUID,
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=primary_key,
    )


def _service(*, nullable: bool = False, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        "service_name",
        sa.String(_SERVICE_NAME_LENGTH),
        nullable=nullable,
        primary_key=primary_key,
    )


def _signal(*, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column("signal", sa.String(_LABEL_LENGTH), nullable=False, primary_key=primary_key)


def _user(name: str) -> sa.Column[Any]:
    """A person named on a row, set null when they are deleted: an organization's
    deletion removes its users before its projects."""
    return sa.Column(name, _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


def _timestamp(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        _TIMESTAMP,
        nullable=nullable,
        server_default=sa.text("now()") if now else None,
    )


def upgrade() -> None:
    _projects_gain_detection_settings()
    _create_findings()
    _create_detection_state()
    _create_install_wide_records()
    _set_condition_kinds(_CONDITION_KINDS)
    _notifications_gain_subjects()


def _projects_gain_detection_settings() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "timezone",
            sa.String(_TIMEZONE_NAME_LENGTH),
            nullable=False,
            server_default=_DEFAULT_TIMEZONE,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "detection_sensitivity",
            sa.String(_LABEL_LENGTH),
            nullable=False,
            server_default=_DEFAULT_SENSITIVITY,
        ),
    )
    op.create_check_constraint(
        "detection_sensitivity_is_known",
        "projects",
        f"detection_sensitivity IN ({_SENSITIVITIES})",
    )


def _create_findings() -> None:
    op.create_table(
        "findings",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        _project(),
        sa.Column("scope", sa.String(_LABEL_LENGTH), nullable=False),
        _service(nullable=True),
        # The highest reached; it only rises (FR14).
        sa.Column("severity", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column(
            "state", sa.String(_LABEL_LENGTH), nullable=False, server_default=sa.text("'open'")
        ),
        sa.Column("closed_reason", sa.String(_LABEL_LENGTH), nullable=True),
        # Opened while its service was muted: recorded and shown, never sent (BR9).
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _user("acknowledged_by"),
        _timestamp("acknowledged_at"),
        # Dated apart from the person, so the mark is still counted (FR26) after they go.
        _user("false_alarm_by"),
        _timestamp("false_alarm_at"),
        # Only an admin's mark returns the finding's minutes to the normal (D28).
        sa.Column(
            "false_alarm_returns_minutes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("feedback_reason", sa.Text(), nullable=True),
        # Rewritten in place as the worst value grows (FR17).
        sa.Column("title", sa.Text(), nullable=False),
        # Each signal's normal, current and worst values and the comparison, so a
        # finding stays explainable after its readings expire (AC40). A list, leading
        # signal first (`nexovue_platform.detection`).
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        _timestamp("started_at", nullable=False),
        _timestamp("closed_at"),
        _timestamp("updated_at", nullable=False, now=True),
        sa.CheckConstraint(f"scope IN ({_FINDING_SCOPES})", name="scope_is_known"),
        sa.CheckConstraint(f"severity IN ({_SEVERITIES})", name="severity_is_known"),
        sa.CheckConstraint(f"state IN ({_FINDING_STATES})", name="state_is_known"),
        sa.CheckConstraint(
            f"closed_reason IS NULL OR closed_reason IN ({_CLOSED_REASONS})",
            name="closed_reason_is_known",
        ),
        sa.CheckConstraint(
            "(scope = 'service') = (service_name IS NOT NULL)", name="service_matches_scope"
        ),
        sa.CheckConstraint("(state = 'open') = (closed_reason IS NULL)", name="closed_says_how"),
        sa.CheckConstraint("(state = 'open') = (closed_at IS NULL)", name="closed_says_when"),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= started_at", name="closes_after_it_starts"
        ),
        sa.CheckConstraint(
            "acknowledged_by IS NULL OR acknowledged_at IS NOT NULL",
            name="acknowledgement_is_dated",
        ),
        sa.CheckConstraint(
            "false_alarm_at IS NOT NULL "
            "OR (false_alarm_by IS NULL AND NOT false_alarm_returns_minutes)",
            name="false_alarm_is_dated",
        ),
    )
    op.create_index(
        "uq_findings_project_id_service_name_open",
        "findings",
        ["project_id", "service_name"],
        unique=True,
        postgresql_where=sa.text("scope = 'service' AND state = 'open'"),
    )
    op.create_index(
        "uq_findings_project_id_open",
        "findings",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'project' AND state = 'open'"),
    )
    # The feed: a project's findings in a state, newest first.
    op.create_index(
        "ix_findings_org_id_project_id_state_started_at",
        "findings",
        ["org_id", "project_id", "state", sa.text("started_at DESC")],
    )
    # A service's findings over time: its timeline (FR30) and the flap guard (C7).
    op.create_index(
        "ix_findings_project_id_service_name_started_at",
        "findings",
        ["project_id", "service_name", "started_at"],
    )

    op.create_table(
        "finding_incidents",
        _org(),
        sa.Column(
            "finding_id",
            _UUID,
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "incident_id",
            _UUID,
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_finding_incidents_incident_id", "finding_incidents", ["incident_id"])


def _create_detection_state() -> None:
    # `org_id` leads every key an upsert names, so no conflict can reach another
    # tenant's row (design, *Findings and their state*).
    op.create_table(
        "detection_state",
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _project(primary_key=True),
        _service(primary_key=True),
        # Null until the service's first minute is judged.
        _timestamp("last_judged_minute"),
        # Each signal's phase, clear-minute count, false-alarm hold and learning date.
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        _timestamp("created_at", nullable=False, now=True),
    )

    op.create_table(
        "detection_project_runs",
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _project(primary_key=True),
        _timestamp("last_completed_at", nullable=False),
        # Set when the owners are told detection stopped; cleared when told it runs again.
        _timestamp("stopped_reported_at"),
    )

    op.create_table(
        "detection_baselines",
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _project(primary_key=True),
        _service(primary_key=True),
        _signal(primary_key=True),
        sa.Column("minute", _TIMESTAMP, primary_key=True),
        # The mode, days, median, spread, burst profile and L the minute is judged by.
        sa.Column("baseline", postgresql.JSONB(), nullable=False),
        _timestamp("computed_at", nullable=False, now=True),
        sa.CheckConstraint(f"signal IN ({_SIGNALS})", name="signal_is_known"),
    )

    op.create_table(
        "baseline_shifts",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        _project(),
        _service(),
        _signal(),
        _timestamp("from_minute", nullable=False),
        # The median of the last 60 readings over the normal (design, *Level shifts*).
        sa.Column("ratio", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(_LABEL_LENGTH), nullable=False),
        _timestamp("created_at", nullable=False, now=True),
        sa.CheckConstraint(f"signal IN ({_SIGNALS})", name="signal_is_known"),
        sa.CheckConstraint(f"reason IN ({_SHIFT_REASONS})", name="reason_is_known"),
        sa.CheckConstraint("ratio > 0", name="ratio_is_positive"),
    )
    op.create_index(
        "ix_baseline_shifts_project_id_service_name_signal_from_minute",
        "baseline_shifts",
        ["project_id", "service_name", "signal", "from_minute"],
    )

    op.create_table(
        "service_detection_settings",
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _project(primary_key=True),
        _service(primary_key=True),
        # Admins, at most 30 days ahead (BR9); nothing is sent and the normal still learns.
        _timestamp("muted_until"),
        _user("muted_by"),
        # Members, at most 4 hours ahead (D25); snoozed minutes stay out of the normal.
        _timestamp("snoozed_until"),
        _user("snoozed_by"),
    )


def _create_install_wide_records() -> None:
    op.create_table(
        "intake_refusals",
        sa.Column("minute", _TIMESTAMP, primary_key=True),
        sa.Column("receiver_id", sa.String(_RECEIVER_ID_LENGTH), primary_key=True),
        sa.Column("reason", sa.String(_LABEL_LENGTH), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"reason IN ({_INTAKE_REFUSAL_REASONS})", name="reason_is_known"),
        sa.CheckConstraint("count > 0", name="count_is_positive"),
    )

    op.create_table(
        "detection_events",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _timestamp("at", nullable=False, now=True),
        sa.Column("kind", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_detection_events_at", "detection_events", ["at"])


def _set_condition_kinds(allowed: str) -> None:
    for table in ("alert_rules", "incidents"):
        op.drop_constraint("condition_is_known", table, type_="check")
        op.create_check_constraint("condition_is_known", table, f"condition_kind IN ({allowed})")


def _notifications_gain_subjects() -> None:
    op.add_column(
        "notifications",
        sa.Column(
            "finding_id",
            _UUID,
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.alter_column("notifications", "incident_id", existing_type=_UUID, nullable=True)
    _set_notification_kinds(_NOTIFICATION_KINDS)
    op.create_check_constraint(
        "names_one_subject",
        "notifications",
        "num_nonnulls(incident_id, finding_id) = "
        f"CASE WHEN kind IN ({_PROJECT_NOTIFICATION_KINDS}) THEN 0 ELSE 1 END",
    )
    op.create_unique_constraint(
        "uq_notifications_finding_id_channel_id_kind_sequence",
        "notifications",
        ["finding_id", "channel_id", "kind", "sequence"],
    )
    op.create_index(
        "uq_notifications_project_id_channel_id_kind_sequence_project",
        "notifications",
        ["project_id", "channel_id", "kind", "sequence"],
        unique=True,
        postgresql_where=sa.text("incident_id IS NULL AND finding_id IS NULL"),
    )


def _set_notification_kinds(allowed: str) -> None:
    op.drop_constraint("kind_is_known", "notifications", type_="check")
    op.create_check_constraint("kind_is_known", "notifications", f"kind IN ({allowed})")


def downgrade() -> None:
    op.execute(
        "DELETE FROM notifications "
        f"WHERE incident_id IS NULL OR kind NOT IN ({_NOTIFICATION_KINDS_BEFORE})"
    )
    op.drop_index("uq_notifications_project_id_channel_id_kind_sequence_project", "notifications")
    op.drop_constraint(
        "uq_notifications_finding_id_channel_id_kind_sequence", "notifications", type_="unique"
    )
    op.drop_constraint("names_one_subject", "notifications", type_="check")
    _set_notification_kinds(_NOTIFICATION_KINDS_BEFORE)
    op.alter_column("notifications", "incident_id", existing_type=_UUID, nullable=False)
    op.drop_column("notifications", "finding_id")

    op.execute("DELETE FROM incidents WHERE condition_kind = 'anomaly_finding'")
    op.execute("DELETE FROM alert_rules WHERE condition_kind = 'anomaly_finding'")
    _set_condition_kinds(_CONDITION_KINDS_BEFORE)

    for table in _NEW_TABLES:
        op.drop_table(table)

    op.drop_constraint("detection_sensitivity_is_known", "projects", type_="check")
    op.drop_column("projects", "detection_sensitivity")
    op.drop_column("projects", "timezone")
