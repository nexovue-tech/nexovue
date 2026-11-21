"""AI connections, opt-outs, usage and runs, and the four job functions of the AI account

F3 (`docs/features/ai-insights/`), plan Step 3 (T3.2): the fence before any provider code.

**Why this change is required.** AI work runs in its own role, `scheduler-insight`, as its
own Postgres account `nexovue_insight` (design, *The Postgres account*; ADR-040). That
account holds no right on `jobs` or `job_results`, because a right to change a table is a
right to `LOCK` it, and those two tables carry every alert. Instead it is granted these
four functions, which do for the `insight` queue alone what `jobs/queue.py`'s `_CLAIM`,
`_RETRY`, `_FINISH` and `jobs/results.py`'s `_STORE_RESULT` do for every queue:

- `insight_claim_jobs(worker, limit, grace)`, `insight_retry_job(id, worker, countdown,
  error)`, `insight_finish_job(id, worker)`: the same statements with `queue = 'insight'`
  written in. `test_insight_account_live.py` runs one table of cases through both and
  holds them equal (AC44).
- `insight_store_check_result(request_id, org_id, payload, ttl_seconds)`: `_STORE_RESULT`
  with `kind = 'ai_check'` written in; the first result still stands.

Each is `SECURITY DEFINER`, so it runs with its owner's rights, with `search_path` fixed to
`pg_catalog, pg_temp` and every table named with its schema, so nothing the caller creates
can stand in for a table or an operator. `EXECUTE` is taken from `PUBLIC` here; `setup`
grants it to the AI account, and takes it from `PUBLIC` again after a restore, which drops
privileges (`restore-postgres.sh --no-privileges`).

The tables are the design's: `ai_connections` (one per organization; its sealed key joins
with the `ai` ring in plan Step 4), `ai_project_opt_outs`, `ai_usage_monthly` and
`ai_runs`, each closed list a `CHECK` (AC96). `job_results.kind` gains `ai_check`.

**Affected store and schema.** Postgres: four tables, four functions, one check widened.

**Compatibility and backfill.** None needed: new tables, and a check widened `NOT VALID`
then validated, so `job_results` is never locked against writes while rows are checked.

**Rollback and recovery.** Roll forward only: `ai_runs` records where an organization's
facts were sent, and `ai_connections` an owner's consent, found nowhere else. Going back
past 0070 is a restore.

**Tenant, retention, and security impact.** Every table carries `org_id` and is deleted
with the organization (`organizations/tables.py`); an opt-out goes with its project. A
run keeps its row when its project or its person goes (`SET NULL`). No table holds a
prompt, a response or telemetry text (FD9). The functions reach only `insight` jobs and
`ai_check` results, whoever calls them.

Revision ID: 0070
Revises: 0069
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.ai_records`'s lists and `models.JOB_RESULT_KINDS`, held equal
# by `test_ai_vocabulary.py`: a later change to them is a migration of its own.
_WIRE_FORMATS = "'openai_chat', 'anthropic_messages', 'bedrock_converse'"
_OFF_REASONS = "'person', 'consent_ended'"
_RUN_PURPOSES = "'explain', 'check', 'chat', 'fix'"
_RUN_OUTCOMES = (
    "'started', 'done', 'abstained', 'unavailable', 'limited', 'bad_key', 'refused', "
    "'too_long', 'invalid', 'died'"
)
_EARLIER_JOB_RESULT_KINDS = "'check_run', 'channel_test', 'connection_test'"
_JOB_RESULT_KINDS = "'check_run', 'channel_test', 'connection_test', 'ai_check'"

# The four functions, in step with `jobs/queue.py` and `jobs/results.py` (AC44). No parameter
# shares a column's name: inside a SQL function a column name wins.
_FUNCTIONS = (
    """
    CREATE FUNCTION insight_claim_jobs(worker text, claim_limit integer, grace integer)
    RETURNS TABLE (
        id uuid, task text, kwargs jsonb, attempts integer, max_attempts integer,
        soft_limit_seconds integer, hard_limit_seconds integer
    )
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        WITH due AS MATERIALIZED (
            SELECT id FROM public.jobs
            WHERE queue = 'insight'
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
    """
    CREATE FUNCTION insight_retry_job(
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
        WHERE id = job_id AND leased_by = worker AND queue = 'insight'
    $$
    """,
    """
    CREATE FUNCTION insight_finish_job(job_id uuid, worker text)
    RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        DELETE FROM public.jobs WHERE id = job_id AND leased_by = worker AND queue = 'insight'
    $$
    """,
    """
    CREATE FUNCTION insight_store_check_result(
        for_request uuid, for_org uuid, result_payload jsonb, ttl_seconds double precision
    )
    RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        INSERT INTO public.job_results (request_id, org_id, kind, payload, expires_at)
        VALUES (
            for_request, for_org, 'ai_check', result_payload,
            now() + make_interval(secs => ttl_seconds)
        )
        ON CONFLICT (request_id) DO NOTHING
    $$
    """,
)
_SIGNATURES = (
    "insight_claim_jobs(text, integer, integer)",
    "insight_retry_job(uuid, text, double precision, text)",
    "insight_finish_job(uuid, text)",
    "insight_store_check_result(uuid, uuid, jsonb, double precision)",
)


def upgrade() -> None:
    op.create_table(
        "ai_connections",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("wire_format", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("region_label", sa.Text(), nullable=True),
        sa.Column("key_id", _UUID, nullable=True),
        sa.Column("call_seconds", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("monthly_token_limit", sa.BigInteger(), nullable=True),
        sa.Column("price_input", sa.Numeric(), nullable=True),
        sa.Column("price_output", sa.Numeric(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("off_reason", sa.Text(), nullable=True),
        sa.Column("checked_at", _TIMESTAMP, nullable=True),
        sa.Column("checked_ok", sa.Boolean(), nullable=True),
        sa.Column("checked_fingerprint", sa.Text(), nullable=True),
        sa.Column("consented_by", _UUID, nullable=True),
        sa.Column("consented_at", _TIMESTAMP, nullable=True),
        sa.Column("consented_base_url", sa.Text(), nullable=True),
        sa.Column("consented_fingerprint", sa.Text(), nullable=True),
        sa.Column("consented_schema", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "consented_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (consented_by)",
            name="fk_ai_connections_consented_by",
        ),
        sa.CheckConstraint(f"wire_format IN ({_WIRE_FORMATS})", name="wire_format_is_known"),
        sa.CheckConstraint("call_seconds BETWEEN 30 AND 300", name="call_seconds_in_range"),
        sa.CheckConstraint(
            "monthly_token_limit IS NULL OR monthly_token_limit > 0", name="limit_is_positive"
        ),
        sa.CheckConstraint(
            "(price_input IS NULL OR price_input >= 0) "
            "AND (price_output IS NULL OR price_output >= 0)",
            name="prices_are_not_negative",
        ),
        sa.CheckConstraint(
            f"off_reason IS NULL OR off_reason IN ({_OFF_REASONS})", name="off_reason_is_known"
        ),
        sa.CheckConstraint(
            "NOT enabled OR (monthly_token_limit IS NOT NULL AND consented_at IS NOT NULL "
            "AND consented_base_url = base_url AND consented_fingerprint IS NOT NULL "
            "AND consented_schema IS NOT NULL)",
            name="on_only_with_limit_and_consent",
        ),
    )

    op.create_table(
        "ai_project_opt_outs",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("org_id", "project_id", name="pk_ai_project_opt_outs"),
    )

    op.create_table(
        "ai_usage_monthly",
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("org_id", "month", name="pk_ai_usage_monthly"),
        sa.CheckConstraint(
            "used_tokens >= 0 AND reserved_tokens >= 0", name="tokens_are_not_negative"
        ),
        sa.CheckConstraint("extract(day FROM month) = 1", name="month_is_its_first_day"),
    )

    op.create_table(
        "ai_runs",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "requested_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("subject_id", _UUID, nullable=True),
        sa.Column("explanation_id", _UUID, nullable=True),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column("wire_format", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("region_label", sa.Text(), nullable=True),
        sa.Column("provider_host", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("request_sha256", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("started_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", _TIMESTAMP, nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False, server_default="started"),
        sa.Column(
            "fields_sent",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(f"purpose IN ({_RUN_PURPOSES})", name="purpose_is_known"),
        sa.CheckConstraint(f"outcome IN ({_RUN_OUTCOMES})", name="outcome_is_known"),
        sa.CheckConstraint(f"wire_format IN ({_WIRE_FORMATS})", name="wire_format_is_known"),
        sa.CheckConstraint(
            "request_sha256 IS NULL OR request_sha256 ~ '^[0-9a-f]{64}$'",
            name="request_sha256_is_hex",
        ),
        sa.CheckConstraint(
            "coalesce(input_tokens, 0) >= 0 AND coalesce(output_tokens, 0) >= 0 "
            "AND reserved_tokens >= 0",
            name="tokens_are_not_negative",
        ),
        sa.CheckConstraint("attempt >= 1", name="attempt_counts_from_one"),
    )
    # The settings screen's run list, newest first; and a finding's runs, for the run cap.
    op.create_index(
        "ix_ai_runs_org_id_started_at", "ai_runs", ["org_id", sa.text("started_at DESC")]
    )
    op.create_index("ix_ai_runs_subject_id_started_at", "ai_runs", ["subject_id", "started_at"])

    op.execute("ALTER TABLE job_results DROP CONSTRAINT ck_job_results_kind_is_known")
    op.execute(
        f"ALTER TABLE job_results ADD CONSTRAINT ck_job_results_kind_is_known "
        f"CHECK (kind IN ({_JOB_RESULT_KINDS})) NOT VALID"
    )
    op.execute("ALTER TABLE job_results VALIDATE CONSTRAINT ck_job_results_kind_is_known")

    for function in _FUNCTIONS:
        op.execute(function)
    for signature in _SIGNATURES:
        # A new function may be run by anyone until this; `setup` grants it to the AI account.
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: ai_runs records where an organization's facts were sent, and "
        "ai_connections an owner's consent, found nowhere else. Going back past 0070 is a "
        "restore."
    )
