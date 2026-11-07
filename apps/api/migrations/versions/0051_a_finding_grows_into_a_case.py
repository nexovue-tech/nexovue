"""A finding grows into a case

F22 (`docs/features/case-management/`), plan T1.2; follows F6's 0041 to 0050.

**Why this change is required.** A finding closes itself when the signal recovers
(0041), so it cannot also say "someone is working on this". A case is columns on
the finding (BR1, FD12, proposed ADR *A case is a finding's columns*): who took
it, when, how it ended, and which later finding joined it. The timeline
(`finding_events`), people's notes (`finding_notes`) and the emails the sweep owes
(`finding_emails_owed`) each hang off the finding.

**Affected store and schema.** Postgres.

- `users` and `findings` gain `UNIQUE (org_id, id)`, and `findings` also
  `UNIQUE (org_id, project_id, id)`, so the new tables can key on both and the
  database itself refuses a cross-tenant assignee, actor, author or root, and a
  cross-project root (D34, AC72). Built concurrently, then taken over by a
  constraint (`ADD CONSTRAINT … USING INDEX`), as 0050 did, because both tables
  hold rows and a plain unique build blocks their writes.
- `findings` gains the case columns, their checks (frozen vocabulary below), two
  partial unique indexes holding BR7's one active case per service and per
  project (D21, mirroring 0041's open indexes), and four organization-wide
  partial indexes for the case filters, the sweep and the open-joined rank. All
  indexes on `findings` are built concurrently.
- `assignee_id` and `case_resolved_by` reference `users (org_id, id)` with
  `ON DELETE SET NULL (column)`; `root_finding_id` references
  `findings (org_id, project_id, id)` the same way. The column list form needs
  Postgres 15; the install runs 18 (`compose.infra.yaml`).
- `finding_events` is append-only: kinds from the frozen vocabulary, an actor
  (person, system or tracker), and a `detail` written only by
  `nexovue_platform.cases.finding_event_parameters`, which admits no free text.
- `finding_notes` holds what people wrote; the author survives a member's
  scrub as *Former member* (BR16). A delete is a real DELETE.
- `finding_emails_owed` is a row per owed email (D90), deleted before its send.

**Compatibility and backfill.** None: no existing finding has a case, and null
case columns are the honest state of every existing row. Nexovue is
pre-deployment, so there is no old data to migrate.

**Rollback and recovery.** Roll forward only: notes and the timeline are people's
words and the organization's record, existing nowhere else. Going back past this
revision is a restore. A failed concurrent build leaves an invalid index; the
upgrade drops one of the same name first, so it can be run again when quiet.

**Tenant, retention, and security impact.** Every new table carries `org_id` in a
composite key, so the database refuses cross-tenant rows outright. The three new
tables cascade from `findings` (`organizations/tables.py`), whose retention F22's
Step 2 amends; until then a finding with a case follows the existing rule. No
secrets, and no free text outside `finding_notes.body`, which is the point of the
table.

Revision ID: 0051
Revises: 0050
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
_LABEL_LENGTH = 64
# FR14's note cap, enforced here as well as at the edge so no other writer can
# store a document where a note belongs.
_NOTE_LENGTH = 4000

# Frozen copies of `nexovue_platform.detection` and `nexovue_platform.cases`,
# held equal to them by `test_alert_vocabulary.py`.
_CASE_STATES = "'investigating', 'resolved'"
_CASE_RESOLUTIONS = (
    "'resolved', 'false_alarm', 'new_normal', 'tracker_closed', 'no_activity', "
    "'recovered_untouched'"
)
_FINDING_EVENT_KINDS = (
    "'finding_opened', 'severity_raised', 'signal_joined', 'finding_closed', "
    "'flapping_started', 'finding_joined_case', 'finding_left_case', 'case_started', "
    "'acknowledged', 'assigned', 'unassigned', 'case_resolved', 'case_reopened', "
    "'false_alarm', 'new_normal', 'note_added', 'note_deleted', 'link_added', "
    "'link_removed', 'link_promoted', 'issue_created', 'issue_closed', "
    "'issue_reopened', 'issue_gone', 'issue_moved', 'tracker_write_failed', "
    "'explained'"
)
_EVENT_ACTOR_KINDS = "'person', 'system', 'tracker'"
_EMAIL_OWED_KINDS = "'tracker_closed', 'tracker_closed_signal_open', 'idle_warning'"


def _concurrent_unique_constraint(table: str, name: str, columns: Sequence[str]) -> None:
    """A unique constraint on a table with rows, without stopping its writes: the
    index is built concurrently and the constraint takes it over (as 0050).

    A run that built the constraint and failed before Alembic recorded the
    revision finds it and leaves it be; a failed build's invalid index is
    dropped first, so running again when the table is quiet recovers."""
    already = op.get_bind().execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": name}
    )
    if already.scalar() is not None:
        return
    _concurrent_index(table, name, columns, unique=True)
    op.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE USING INDEX {name}"))


def _concurrent_index(
    table: str,
    name: str,
    columns: Sequence[str],
    *,
    unique: bool = False,
    where: str | None = None,
) -> None:
    """Build an index on a table with rows without stopping its writes, dropping a
    failed earlier build's invalid leftover first (`docs/engineering/database.md`)."""
    op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
    uniqueness = "UNIQUE " if unique else ""
    predicate = "" if where is None else f" WHERE {where}"
    op.execute(
        sa.text(
            f"CREATE {uniqueness}INDEX CONCURRENTLY {name} "
            f"ON {table} ({', '.join(columns)}){predicate}"
        )
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _concurrent_unique_constraint("users", "uq_users_org_id_id", ["org_id", "id"])
        _concurrent_unique_constraint("findings", "uq_findings_org_id_id", ["org_id", "id"])
        _concurrent_unique_constraint(
            "findings", "uq_findings_org_id_project_id_id", ["org_id", "project_id", "id"]
        )

    _findings_grow_case_columns()
    _finding_events()
    _finding_notes()
    _finding_emails_owed()

    with op.get_context().autocommit_block():
        # BR7, D21: one active case per service and one per project, mirroring
        # 0041's open indexes because Postgres treats nulls as distinct.
        _concurrent_index(
            "findings",
            "uq_findings_project_service_active_case",
            ["project_id", "service_name"],
            unique=True,
            where="scope = 'service' AND case_state = 'investigating'",
        )
        _concurrent_index(
            "findings",
            "uq_findings_project_active_case",
            ["project_id"],
            unique=True,
            where="scope = 'project' AND case_state = 'investigating'",
        )
        # The case filters read across the organization; the two indexes above
        # begin with project_id, so they cannot serve those reads.
        _concurrent_index(
            "findings",
            "ix_findings_org_id_assignee_id",
            ["org_id", "assignee_id"],
            where="assignee_id IS NOT NULL",
        )
        _concurrent_index(
            "findings",
            "ix_findings_org_id_case_resolved_at",
            ["org_id", "case_resolved_at DESC"],
            where="case_state = 'resolved'",
        )
        _concurrent_index(
            "findings",
            "ix_findings_org_id_active_case",
            ["org_id"],
            where="case_state = 'investigating'",
        )
        _concurrent_index(
            "findings",
            "ix_findings_org_id_root_finding_id",
            ["org_id", "root_finding_id"],
            where="root_finding_id IS NOT NULL",
        )


def _findings_grow_case_columns() -> None:
    op.add_column("findings", sa.Column("case_state", sa.String(_LABEL_LENGTH), nullable=True))
    op.add_column("findings", sa.Column("case_resolution", sa.String(_LABEL_LENGTH), nullable=True))
    op.add_column("findings", sa.Column("assignee_id", _UUID, nullable=True))
    op.add_column("findings", sa.Column("case_opened_at", _TIMESTAMP, nullable=True))
    op.add_column("findings", sa.Column("case_resolved_at", _TIMESTAMP, nullable=True))
    op.add_column("findings", sa.Column("case_resolved_by", _UUID, nullable=True))
    op.add_column("findings", sa.Column("case_activity_at", _TIMESTAMP, nullable=True))
    op.add_column("findings", sa.Column("case_worked_at", _TIMESTAMP, nullable=True))
    op.add_column("findings", sa.Column("assignee_acted_at", _TIMESTAMP, nullable=True))
    op.add_column("findings", sa.Column("case_warned_at", _TIMESTAMP, nullable=True))
    op.add_column("findings", sa.Column("root_finding_id", _UUID, nullable=True))

    # The composite keys (D34): the database refuses an assignee, resolver or
    # root of another organization, and a root of another project.
    op.create_foreign_key(
        "fk_findings_assignee_id_users",
        "findings",
        "users",
        ["org_id", "assignee_id"],
        ["org_id", "id"],
        ondelete="SET NULL (assignee_id)",
    )
    op.create_foreign_key(
        "fk_findings_case_resolved_by_users",
        "findings",
        "users",
        ["org_id", "case_resolved_by"],
        ["org_id", "id"],
        ondelete="SET NULL (case_resolved_by)",
    )
    op.create_foreign_key(
        "fk_findings_root_finding_id_findings",
        "findings",
        "findings",
        ["org_id", "project_id", "root_finding_id"],
        ["org_id", "project_id", "id"],
        ondelete="SET NULL (root_finding_id)",
    )

    # In 0041's `closed_says_how` style: the pairs that must move together. The
    # table is small and pre-deployment, so the checks are added plainly.
    op.create_check_constraint(
        "case_state_is_known", "findings", f"case_state IS NULL OR case_state IN ({_CASE_STATES})"
    )
    op.create_check_constraint(
        "case_resolution_is_known",
        "findings",
        f"case_resolution IS NULL OR case_resolution IN ({_CASE_RESOLUTIONS})",
    )
    op.create_check_constraint(
        "case_is_dated", "findings", "(case_state IS NULL) = (case_opened_at IS NULL)"
    )
    op.create_check_constraint(
        "case_activity_is_dated", "findings", "(case_state IS NULL) = (case_activity_at IS NULL)"
    )
    op.create_check_constraint(
        "case_resolved_says_when",
        "findings",
        "(case_state IS NOT DISTINCT FROM 'resolved') = (case_resolved_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "case_resolved_says_how",
        "findings",
        "(case_state IS NOT DISTINCT FROM 'resolved') = (case_resolution IS NOT NULL)",
    )
    op.create_check_constraint(
        "assignee_needs_a_case", "findings", "assignee_id IS NULL OR case_state IS NOT NULL"
    )
    # A finding holds a case or joins one, never both (BR1, BR7).
    op.create_check_constraint(
        "holds_or_joins_never_both",
        "findings",
        "case_state IS NULL OR root_finding_id IS NULL",
    )
    op.create_check_constraint(
        "never_its_own_root", "findings", "root_finding_id IS DISTINCT FROM id"
    )


def _finding_events() -> None:
    """The timeline (FR16): append-only, no free text, read by people and tokens."""
    op.create_table(
        "finding_events",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("actor_kind", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("actor_id", _UUID, nullable=True),
        # Written only through `finding_event_parameters()`: enums, ids, numbers.
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("at", _TIMESTAMP, nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_events_finding",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "actor_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (actor_id)",
            name="fk_finding_events_actor",
        ),
        sa.CheckConstraint(f"kind IN ({_FINDING_EVENT_KINDS})", name="kind_is_known"),
        sa.CheckConstraint(f"actor_kind IN ({_EVENT_ACTOR_KINDS})", name="actor_kind_is_known"),
    )
    # The timeline read: a root's events and its joined findings', newest first.
    op.create_index(
        "ix_finding_events_org_id_finding_id_at",
        "finding_events",
        ["org_id", "finding_id", sa.text("at DESC"), sa.text("id DESC")],
    )


def _finding_notes() -> None:
    """What people wrote (FR14, BR16): the organization's working record."""
    op.create_table(
        "finding_notes",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_id", _UUID, nullable=False),
        sa.Column("author_id", _UUID, nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_notes_finding",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "author_id"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (author_id)",
            name="fk_finding_notes_author",
        ),
        sa.CheckConstraint(f"length(body) <= {_NOTE_LENGTH}", name="a_note_is_not_a_document"),
    )
    op.create_index(
        "ix_finding_notes_org_id_finding_id_created_at",
        "finding_notes",
        ["org_id", "finding_id", "created_at"],
    )
    # The personal-data export reads a person's own notes (FR20 of personal data).
    op.create_index("ix_finding_notes_org_id_author_id", "finding_notes", ["org_id", "author_id"])


def _finding_emails_owed() -> None:
    """A row per owed case email (D90): deleted before its send, so a crash loses
    an email rather than repeating one, and a second owed email cannot overwrite
    the first. It holds no name, address or text."""
    op.create_table(
        "finding_emails_owed",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(_LABEL_LENGTH), nullable=False),
        sa.Column("at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_emails_owed_finding",
        ),
        sa.CheckConstraint(f"kind IN ({_EMAIL_OWED_KINDS})", name="kind_is_known"),
    )
    # The sweep reads what is due, oldest first.
    op.create_index("ix_finding_emails_owed_at", "finding_emails_owed", ["at"])


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: finding_notes and finding_events hold people's words and "
        "the organization's record, which exist nowhere else. Going back past 0051 "
        "is a restore."
    )
