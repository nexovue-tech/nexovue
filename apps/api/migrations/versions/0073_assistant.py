"""Chat's conversations, questions, steps and counts, their row security, and its two functions

F4 (`docs/features/ai-chatbot/`), plan Step 2 (T2.1, T2.3).

**Why this change is required.** A person asks chat a question; the API stores it with a
ticket, and the AI role (`nexovue_insight`) answers it through the API. The tables are the
design's: `assistant_settings`, `assistant_conversations`, `assistant_messages` (the
question, its clocks, its sealed ticket, its answer), `assistant_steps`,
`assistant_question_counts`, `assistant_monthly_counts` and `assistant_read_log`. No vector
column: those come from a `setup` step in Step 6 (design, *Extension*).

**Row-level security, F4's own first use** (design, *A plain limit*; D57). Without it the AI
account could read every stored question and answer and rewrite past ones. It sees a
message only while in flight or holding a reservation, and a step only through a message it
sees. Its own writes can only keep a message in flight; **every final write goes through
`assistant_finish_message`**, `SECURITY DEFINER`, because on an `UPDATE` with a `WHERE`
Postgres checks the new row against the `SELECT` policy too, so no policy can let the
account write a row it may then not see (second review's probe). `assistant_record_spend`
moves a call's tokens the same way. Policies name no role, since `nexovue_insight` exists
only once `setup` runs; the owning install account and a superuser are not bound (no
`FORCE`), and the API is that account.

**F3's three job functions gain `assistant`** (design, *Queue and role*; plan C4). The claim
takes the queue list, refusing any name but those two, so each capped lane claims only its
own queue.

**Affected store and schema.** Postgres: seven tables, two functions, F3's three job
functions replaced.

**Compatibility and backfill.** None: new tables; the job functions keep their behaviour
for `insight`.

**Rollback and recovery.** Roll forward only: question counts and the read record are an
organization's record of who asked and what was sent, found nowhere else.

**Tenant, retention, and security impact.** Every table carries `org_id` and goes with its
organization (`organizations/tables.py`); conversations go with their person or project.
The read record keeps its row when its person or project goes. No table holds a tool
result or a prompt (FD9). `ticket_hash` and `sealed_ticket` are nulled when a question ends.

Revision ID: 0073
Revises: 0072
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies of `nexovue_platform.assistant_records`'s lists, held equal by
# `test_assistant_records.py`: a later change to them is a migration of its own.
_IN_FLIGHT = "'queued', 'running'"
_STATES = (
    "'queued', 'running', 'answered', 'partial', 'abstained', 'withheld', 'stopped', "
    "'refused', 'failed', 'over_limit', 'unavailable'"
)
_FINAL_STATES = (
    "'answered', 'partial', 'abstained', 'withheld', 'stopped', 'refused', 'failed', "
    "'over_limit', 'unavailable'"
)
_PHASES = "'waiting', 'asking', 'reading', 'checking'"
_FAILURES = (
    "'ai_off', 'chat_off', 'opted_out', 'consent_changed', 'ticket_refused', 'unavailable', "
    "'limited', 'bad_key', 'not_found', 'bad_request', 'refused', 'too_long', 'invalid', "
    "'timed_out', 'unreachable', 'address_refused', 'not_allowed', 'key_unreadable', "
    "'no_connection', 'soft_limit', 'died'"
)
_REJECTIONS = "'shape', 'citations', 'way_out', 'command', 'copying'"
_ABSTAINS = "'not_enough_evidence', 'no_data', 'outside_what_it_reads'"
_WRONGS = "'wrong_numbers', 'wrong_cause', 'missed_something', 'other'"
_STEP_STATES = "'running', 'read', 'failed', 'aged_out', 'refused'"
_DATA_LEVELS = "'facts', 'facts_and_text'"

_POLICIES = (
    "ALTER TABLE assistant_messages ENABLE ROW LEVEL SECURITY",
    f"""
    CREATE POLICY assistant_messages_in_flight_or_held ON assistant_messages FOR SELECT
    USING (state IN ({_IN_FLIGHT}) OR reserved_tokens > 0)
    """,
    f"""
    CREATE POLICY assistant_messages_stay_in_flight ON assistant_messages FOR UPDATE
    USING (state IN ({_IN_FLIGHT})) WITH CHECK (state IN ({_IN_FLIGHT}))
    """,
    "ALTER TABLE assistant_steps ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY assistant_steps_of_a_visible_message ON assistant_steps FOR ALL
    USING (EXISTS (SELECT 1 FROM assistant_messages AS m WHERE m.id = message_id))
    WITH CHECK (EXISTS (SELECT 1 FROM assistant_messages AS m WHERE m.id = message_id))
    """,
)

# No parameter shares a column's name: inside a function a column name wins.
_FINISH = f"""
CREATE FUNCTION assistant_finish_message(
    for_message uuid, final_state text, final_answer jsonb, final_failure text,
    final_rejection text, final_abstain text, remainder text
)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    held public.assistant_messages%ROWTYPE;
    charged bigint;
BEGIN
    IF remainder NOT IN ('return', 'charge', 'keep') THEN
        RAISE EXCEPTION 'remainder must be return, charge or keep';
    END IF;
    IF final_state NOT IN ({_FINAL_STATES}) THEN
        RAISE EXCEPTION 'not a final state';
    END IF;
    -- 1. Only a row in flight or still holding a reservation: an answer, once final,
    -- is never rewritten (BR14).
    SELECT * INTO held FROM public.assistant_messages
    WHERE id = for_message AND (state IN ({_IN_FLIGHT}) OR reserved_tokens > 0)
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    -- 2. The state, compare-and-set: a stopped or unavailable row keeps its own.
    IF held.state IN ({_IN_FLIGHT}) THEN
        UPDATE public.assistant_messages SET
            state = final_state, phase = NULL, finished_at = now(), answer = final_answer,
            failure_reason = final_failure, rejection_reason = final_rejection,
            abstain_reason = final_abstain, ticket_hash = NULL, sealed_ticket = NULL,
            history = NULL
        WHERE id = for_message;
        UPDATE public.assistant_steps SET state = 'failed', finished_at = now()
        WHERE message_id = for_message AND state = 'running';
    END IF;
    -- 3. What it still holds: returned, charged as spent (estimated), or kept.
    IF remainder <> 'keep' AND held.reserved_tokens > 0 THEN
        charged := CASE WHEN remainder = 'charge' THEN held.reserved_tokens ELSE 0 END;
        UPDATE public.ai_usage_monthly SET
            reserved_tokens = greatest(reserved_tokens - held.reserved_tokens, 0),
            used_tokens = used_tokens + charged
        WHERE org_id = held.org_id AND month = held.reserved_month;
        UPDATE public.assistant_messages SET
            reserved_tokens = 0, used_tokens = used_tokens + charged,
            tokens_estimated = tokens_estimated OR charged > 0
        WHERE id = for_message;
    END IF;
    RETURN held.state IN ({_IN_FLIGHT});
END
$$
"""

_RECORD_SPEND = f"""
CREATE FUNCTION assistant_record_spend(for_message uuid, spent bigint, estimated boolean)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    held public.assistant_messages%ROWTYPE;
    released bigint;
BEGIN
    IF spent < 0 THEN
        RAISE EXCEPTION 'spent cannot be negative';
    END IF;
    SELECT * INTO held FROM public.assistant_messages
    WHERE id = for_message AND (state IN ({_IN_FLIGHT}) OR reserved_tokens > 0)
    FOR UPDATE;
    IF NOT FOUND OR held.reserved_month IS NULL THEN
        RETURN;
    END IF;
    -- Actual may pass the reservation: it is still counted, F3 BR13's one overshoot.
    released := least(spent, held.reserved_tokens);
    UPDATE public.ai_usage_monthly SET
        reserved_tokens = greatest(reserved_tokens - released, 0),
        used_tokens = used_tokens + spent
    WHERE org_id = held.org_id AND month = held.reserved_month;
    UPDATE public.assistant_messages SET
        reserved_tokens = reserved_tokens - released, used_tokens = used_tokens + spent,
        tokens_estimated = tokens_estimated OR estimated
    WHERE id = for_message;
END
$$
"""

# F3's job functions, now for both AI queues. The claim takes the lane's queues and refuses
# any other name by never matching it.
_AI_QUEUES = "'insight', 'assistant'"
_JOB_FUNCTIONS = (
    "DROP FUNCTION insight_claim_jobs(text, integer, integer)",
    f"""
    CREATE FUNCTION insight_claim_jobs(
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
_SIGNATURES = (
    "insight_claim_jobs(text, text[], integer, integer)",
    "assistant_finish_message(uuid, text, jsonb, text, text, text, text)",
    "assistant_record_spend(uuid, bigint, boolean)",
)


def _org() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "assistant_settings",
        sa.Column(
            "org_id",
            _UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("daily_question_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("data_level", sa.Text(), nullable=False, server_default="facts"),
        # FR40: email addresses masked before anything is sent; on unless an admin says.
        sa.Column("mask_emails", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consented_by", _UUID, nullable=True),
        sa.Column("consented_at", _TIMESTAMP, nullable=True),
        sa.Column("consented_fingerprint", sa.Text(), nullable=True),
        sa.Column("consented_disclosure_version", sa.Integer(), nullable=True),
        sa.Column("consented_data_level", sa.Text(), nullable=True),
        sa.Column("updated_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "consented_by"],
            ["users.org_id", "users.id"],
            ondelete="SET NULL (consented_by)",
            name="fk_assistant_settings_consented_by",
        ),
        sa.CheckConstraint("daily_question_limit BETWEEN 1 AND 1000", name="daily_limit_in_range"),
        sa.CheckConstraint(f"data_level IN ({_DATA_LEVELS})", name="data_level_is_known"),
        sa.CheckConstraint(
            f"consented_data_level IS NULL OR consented_data_level IN ({_DATA_LEVELS})",
            name="consented_data_level_is_known",
        ),
    )

    op.create_table(
        "assistant_conversations",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("last_message_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
            name="fk_assistant_conversations_user",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_assistant_conversations_project",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_assistant_conversations_org_id_id"),
    )
    op.create_index(
        "ix_assistant_conversations_user_project_last",
        "assistant_conversations",
        ["user_id", "project_id", sa.text("last_message_at DESC")],
    )

    op.create_table(
        "assistant_messages",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("project_id", _UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("history", postgresql.JSONB(), nullable=True),
        sa.Column("data_level", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("phase", sa.Text(), nullable=True, server_default="waiting"),
        sa.Column("answer", postgresql.JSONB(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("abstain_reason", sa.Text(), nullable=True),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_month", sa.Date(), nullable=True),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("wrong_reason", sa.Text(), nullable=True),
        sa.Column("ticket_hash", sa.Text(), nullable=True),
        sa.Column("sealed_ticket", sa.LargeBinary(), nullable=True),
        sa.Column("call_seconds", sa.Integer(), nullable=False),
        sa.Column("queue_expires_at", _TIMESTAMP, nullable=False),
        sa.Column("deadline_at", _TIMESTAMP, nullable=False),
        sa.Column("hard_ends_at", _TIMESTAMP, nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", _TIMESTAMP, nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "conversation_id"],
            ["assistant_conversations.org_id", "assistant_conversations.id"],
            ondelete="CASCADE",
            name="fk_assistant_messages_conversation",
        ),
        sa.CheckConstraint(f"state IN ({_STATES})", name="state_is_known"),
        sa.CheckConstraint(f"phase IS NULL OR phase IN ({_PHASES})", name="phase_is_known"),
        sa.CheckConstraint(f"data_level IN ({_DATA_LEVELS})", name="data_level_is_known"),
        sa.CheckConstraint(
            f"failure_reason IS NULL OR failure_reason IN ({_FAILURES})",
            name="failure_reason_is_known",
        ),
        sa.CheckConstraint(
            f"rejection_reason IS NULL OR rejection_reason IN ({_REJECTIONS})",
            name="rejection_reason_is_known",
        ),
        sa.CheckConstraint(
            f"abstain_reason IS NULL OR abstain_reason IN ({_ABSTAINS})",
            name="abstain_reason_is_known",
        ),
        sa.CheckConstraint(
            f"wrong_reason IS NULL OR wrong_reason IN ({_WRONGS})", name="wrong_reason_is_known"
        ),
        sa.CheckConstraint("char_length(question) BETWEEN 1 AND 2000", name="question_length"),
        sa.CheckConstraint(
            "reserved_tokens >= 0 AND used_tokens >= 0", name="tokens_are_not_negative"
        ),
        sa.CheckConstraint(
            "reserved_month IS NULL OR extract(day FROM reserved_month) = 1",
            name="reserved_month_is_its_first_day",
        ),
        sa.CheckConstraint(
            f"state IN ({_IN_FLIGHT}) OR (ticket_hash IS NULL AND sealed_ticket IS NULL)",
            name="ticket_only_in_flight",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_assistant_messages_org_id_id"),
    )
    # One question in flight per person (FR4). State only: a partial index cannot read the
    # clock, so the API marks a stale row before inserting (design, *Indexes*).
    op.create_index(
        "uq_assistant_one_in_flight",
        "assistant_messages",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(f"state IN ({_IN_FLIGHT})"),
    )
    op.create_index(
        "uq_assistant_messages_ticket_hash",
        "assistant_messages",
        ["ticket_hash"],
        unique=True,
        postgresql_where=sa.text("ticket_hash IS NOT NULL"),
    )
    op.create_index(
        "ix_assistant_messages_conversation_created",
        "assistant_messages",
        ["conversation_id", sa.text("created_at DESC")],
    )
    # The sweep's two scans: rows in flight, or still holding a reservation.
    op.create_index(
        "ix_assistant_messages_unsettled",
        "assistant_messages",
        ["hard_ends_at"],
        postgresql_where=sa.text(f"state IN ({_IN_FLIGHT}) OR reserved_tokens > 0"),
    )

    op.create_table(
        "assistant_steps",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column(
            "message_id",
            _UUID,
            sa.ForeignKey("assistant_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("display_arguments", sa.Text(), nullable=True),
        sa.Column("from_time", _TIMESTAMP, nullable=True),
        sa.Column("to_time", _TIMESTAMP, nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("was_cut", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.Text(), nullable=False, server_default="running"),
        sa.Column("failure", sa.Text(), nullable=True),
        sa.Column("started_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", _TIMESTAMP, nullable=True),
        sa.CheckConstraint(f"state IN ({_STEP_STATES})", name="state_is_known"),
        sa.CheckConstraint("position BETWEEN 1 AND 32", name="position_in_range"),
        sa.CheckConstraint(
            "display_arguments IS NULL OR char_length(display_arguments) <= 200",
            name="display_arguments_length",
        ),
        sa.UniqueConstraint("message_id", "position", name="uq_assistant_steps_position"),
    )

    op.create_table(
        "assistant_question_counts",
        _org(),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("org_id", "user_id", "day", name="pk_assistant_question_counts"),
        sa.CheckConstraint("count >= 0", name="count_is_not_negative"),
    )

    op.create_table(
        "assistant_monthly_counts",
        _org(),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("org_id", "month", "kind", name="pk_assistant_monthly_counts"),
        sa.CheckConstraint("kind ~ '^[a-z_]{1,40}$'", name="kind_is_a_word"),
        sa.CheckConstraint("extract(day FROM month) = 1", name="month_is_its_first_day"),
    )

    op.create_table(
        "assistant_read_log",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column(
            "project_id", _UUID, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("from_time", _TIMESTAMP, nullable=True),
        sa.Column("to_time", _TIMESTAMP, nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("provider_label", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_assistant_read_log_org_at", "assistant_read_log", ["org_id", sa.text("at DESC")]
    )

    for statement in (*_POLICIES, _FINISH, _RECORD_SPEND, *_JOB_FUNCTIONS):
        op.execute(statement)
    for signature in _SIGNATURES:
        # Anyone may run a new function until this; `setup` grants it to the AI account.
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: question counts and the read record are an organization's "
        "record of who asked and what was sent, found nowhere else. Going back past 0073 "
        "is a restore."
    )
