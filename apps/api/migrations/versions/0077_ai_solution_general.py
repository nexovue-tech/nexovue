"""A finding's general fix suggestions, their row security, and the AI queue for them

F5 (`docs/features/ai-solution/`), plan Step 2b (T2b.1, T2b.5).

**Why this change is required.** Where Nexovue cannot read a service's code, a member may ask
for a general fix suggestion (rung 2b, spec FR31 to FR38). The API stores the request as a
`finding_fixes` row in `proposing`, and the AI role (`nexovue_insight`) writes its answer,
after G1 to G9, onto that row. The first of F5's three migrations (design, *Data and migration
impact*): only what rung 2b needs. Step 3 adds the source columns, `source` to `kind`, and
the CHECK that a general row holds none of them.

**Row-level security** (design, *The Postgres account*). The AI account may read a row in any
of rung 2b's states and change one only while it is `proposing`, into one of them. The
`SELECT` policy admits the end states because Postgres applies it to the new row of an
`UPDATE` too, so a `proposing`-only policy would refuse the move to `proposed`. Policies
name no role, since `nexovue_insight` exists only once `setup` runs; the owning install
account (the API) is not bound. Grants live in `insight_account.INSIGHT_GRANTS`, never here.

**F3's three job functions gain `insight-fix`** (D40, D56): the queue rung 2b's job runs on,
capped apart so suggestions never hold every explanation slot.

**`fix_proposed` joins the timeline's kinds** (design: at Step 2b), holding ids and counts.

**Affected store and schema.** Postgres: one table, two policies, the timeline's kind check
widened, F3's three job functions replaced.

**Compatibility and backfill.** None: a new table; the kind check only widens, replaced
`NOT VALID` then validated; the job functions keep their behaviour for both earlier queues.

**Rollback and recovery.** Roll forward only: a suggestion is the organization's record of
what was sent to its provider and what came back, found nowhere else.

**Tenant, retention, and security impact.** Every row carries `org_id`, bound by composite
keys to its finding, project and requester, and goes with its finding
(`organizations/tables.py`). It holds no source, so BR9's code deletion does not apply
(FR38). The requester's id is set null when they are removed; no person's words are stored.

Revision ID: 0077
Revises: 0076
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.fixes.records`'s lists, held equal by
# `test_fix_general_storage.py`: a later change to them is a migration of its own.
_FIX_KINDS = "'general'"
_FIX_STATES = "'proposing', 'proposed', 'refused', 'failed'"
_ENDED_STATES = "'refused', 'failed'"
_GENERAL_LANGUAGES = (
    "'python', 'javascript', 'typescript', 'java', 'kotlin', 'go', 'ruby', 'php', 'csharp', 'rust'"
)
_FIX_REASONS = (
    "'evidence_too_thin', 'needs_a_decision', 'not_a_code_problem', 'general_withheld_G3', "
    "'general_withheld_G4', 'general_withheld_G5', 'general_withheld_G6', "
    "'general_withheld_G7', 'general_withheld_G8', 'general_withheld_G9', 'ai_off', "
    "'project_opted_out', 'consent_ended_in_flight', 'limit_reached', 'provider_refused', "
    "'provider_too_long', 'provider_unavailable', 'invalid_output', 'timed_out', "
    "'nexovue_error'"
)
_RETRY_CODES = "'general_shape', 'general_too_long'"
_GENERAL_SUGGESTION_MAX_BYTES = 8192
# F22's timeline vocabulary (0051) with F5's kind, held equal by `test_alert_vocabulary.py`.
_FINDING_EVENT_KINDS = (
    "'finding_opened', 'severity_raised', 'signal_joined', 'finding_closed', "
    "'flapping_started', 'finding_joined_case', 'finding_left_case', 'case_started', "
    "'acknowledged', 'assigned', 'unassigned', 'case_resolved', 'case_reopened', "
    "'false_alarm', 'new_normal', 'note_added', 'note_deleted', 'link_added', "
    "'link_removed', 'link_promoted', 'issue_created', 'issue_closed', 'issue_reopened', "
    "'issue_gone', 'issue_moved', 'tracker_write_failed', 'explained', 'fix_proposed'"
)
_EVENT_KIND_CHECK = "ck_finding_events_kind_is_known"

_POLICIES = (
    "ALTER TABLE finding_fixes ENABLE ROW LEVEL SECURITY",
    f"""
    CREATE POLICY finding_fixes_ai_reads ON finding_fixes FOR SELECT
    USING (state IN ({_FIX_STATES}))
    """,
    f"""
    CREATE POLICY finding_fixes_ai_ends_proposing ON finding_fixes FOR UPDATE
    USING (state = 'proposing') WITH CHECK (state IN ({_FIX_STATES}))
    """,
)

# F3's job functions, for all three AI queues. The claim takes the lane's queues and refuses
# any other name by never matching it (0073's form, one queue more).
_AI_QUEUES = "'insight', 'assistant', 'insight-fix'"
_JOB_FUNCTIONS = (
    f"""
    CREATE OR REPLACE FUNCTION insight_claim_jobs(
        worker text, claim_queues text[], claim_limit integer, grace integer
    )
    RETURNS TABLE (
        id uuid, task text, kwargs jsonb, attempts integer, max_attempts integer,
        soft_limit_seconds integer, hard_limit_seconds integer
    )
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        WITH due AS MATERIALIZED (
            SELECT id FROM public.jobs
            WHERE queue = ANY(claim_queues)
              AND queue IN ({_AI_QUEUES})
              AND run_after <= now()
              AND (lease_until IS NULL OR lease_until < now())
              AND (expires_at IS NULL OR expires_at > now())
              AND attempts < max_attempts
            ORDER BY run_after
            LIMIT claim_limit
            FOR UPDATE SKIP LOCKED
        )
        UPDATE public.jobs AS claimed SET
            lease_until = now() + make_interval(secs => claimed.hard_limit_seconds + grace),
            leased_by = worker,
            attempts = claimed.attempts + 1
        FROM due
        WHERE claimed.id = due.id
        RETURNING claimed.id, claimed.task, claimed.kwargs, claimed.attempts,
                  claimed.max_attempts, claimed.soft_limit_seconds, claimed.hard_limit_seconds
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION insight_retry_job(
        job_id uuid, worker text, countdown double precision, error text
    )
    RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        UPDATE public.jobs SET
            run_after = now() + make_interval(secs => countdown),
            lease_until = NULL,
            leased_by = NULL,
            last_error = error
        WHERE id = job_id AND leased_by = worker AND queue IN ({_AI_QUEUES})
    $$
    """,
    f"""
    CREATE OR REPLACE FUNCTION insight_finish_job(job_id uuid, worker text)
    RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        DELETE FROM public.jobs
        WHERE id = job_id AND leased_by = worker AND queue IN ({_AI_QUEUES})
    $$
    """,
)


def _org() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "finding_fixes",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("finding_id", _UUID, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        # The explanation version whose `model_input` was sent (FR34).
        sa.Column("explanation_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="proposing"),
        # The sweep's clock (design): when the state last moved.
        sa.Column("state_changed_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("retry_code", sa.Text(), nullable=True),
        sa.Column("general_language", sa.Text(), nullable=True),
        sa.Column("general_suggestion", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        # When the sweep wrote the row's event or audit entry: safe to repeat (design).
        sa.Column("result_recorded_at", _TIMESTAMP, nullable=True),
        sa.Column("requested_by", _UUID, nullable=True),
        sa.Column("requested_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", _TIMESTAMP, nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "finding_id"],
            ["findings.org_id", "findings.id"],
            ondelete="CASCADE",
            name="fk_finding_fixes_finding",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_finding_fixes_project",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "requested_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (requested_by)",
            name="fk_finding_fixes_requested_by",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_finding_fixes_org_id_id"),
        sa.CheckConstraint(f"kind IN ({_FIX_KINDS})", name="kind_is_known"),
        sa.CheckConstraint(f"state IN ({_FIX_STATES})", name="state_is_known"),
        sa.CheckConstraint(f"reason IS NULL OR reason IN ({_FIX_REASONS})", name="reason_is_known"),
        sa.CheckConstraint(
            f"(state IN ({_ENDED_STATES})) = (reason IS NOT NULL)",
            name="reason_only_when_it_ended_short",
        ),
        sa.CheckConstraint(
            f"retry_code IS NULL OR retry_code IN ({_RETRY_CODES})", name="retry_code_is_known"
        ),
        sa.CheckConstraint(
            f"(kind = 'general') = (general_language IS NOT NULL) "
            f"AND (general_language IS NULL OR general_language IN ({_GENERAL_LANGUAGES}))",
            name="general_language_is_known",
        ),
        # A general row holds an answer only once it passed G1 to G9; refused text is never
        # stored (FR36).
        sa.CheckConstraint(
            "kind <> 'general' OR (state = 'proposed') = (general_suggestion IS NOT NULL)",
            name="general_suggestion_only_when_proposed",
        ),
        sa.CheckConstraint(
            "general_suggestion IS NULL OR "
            f"octet_length(general_suggestion::text) <= {_GENERAL_SUGGESTION_MAX_BYTES}",
            name="general_suggestion_is_small",
        ),
        sa.CheckConstraint("explanation_version >= 1", name="explanation_version_counts_from_one"),
        sa.CheckConstraint("tokens_used >= 0", name="tokens_used_count_up"),
    )
    # BR8: one suggestion in flight per finding; rung 2b counts (FR31).
    op.create_index(
        "uq_finding_fixes_one_in_flight",
        "finding_fixes",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text("state = 'proposing'"),
    )
    # The finding's suggestions, newest first: the read and the tries in a day.
    op.create_index(
        "ix_finding_fixes_org_id_finding_id_requested_at",
        "finding_fixes",
        ["org_id", "finding_id", sa.text("requested_at DESC")],
    )
    # The sweep: rows still in flight, and ended rows whose result is not yet recorded.
    op.create_index(
        "ix_finding_fixes_unrecorded",
        "finding_fixes",
        ["state_changed_at"],
        postgresql_where=sa.text("result_recorded_at IS NULL"),
    )

    for statement in (*_POLICIES, *_JOB_FUNCTIONS):
        op.execute(statement)

    op.execute(f"ALTER TABLE finding_events DROP CONSTRAINT {_EVENT_KIND_CHECK}")
    op.execute(
        f"ALTER TABLE finding_events ADD CONSTRAINT {_EVENT_KIND_CHECK} "
        f"CHECK (kind IN ({_FINDING_EVENT_KINDS})) NOT VALID"
    )
    op.execute(f"ALTER TABLE finding_events VALIDATE CONSTRAINT {_EVENT_KIND_CHECK}")


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: a general suggestion is the organization's record of what was "
        "sent to its AI provider and what came back, found nowhere else. Going back past "
        "0077 is a restore."
    )
