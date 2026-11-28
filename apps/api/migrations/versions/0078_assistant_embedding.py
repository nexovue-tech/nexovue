"""Chat's embedding setting, its second connection, embedding runs, and a question's vector nulled

F4 (`docs/features/ai-chatbot/`), plan Step 6 (T6.1, T6.2).

**Why this change is required.** Meaning search needs an embedding model chosen by an admin,
proved by a real check before anything is embedded, and, for a provider with no embedding
model (Anthropic), an optional second connection for embeddings only, with its own address,
key and an owner's consent (FR27, AC85, D61). `assistant_settings` gains those columns.
Embedding is spending: every call is an `ai_runs` row against the month's reservation, as
F3's wording is, so `ai_runs.purpose` gains `embed`. And a question's own vector, stored on its
step by the worker, must go when the question ends (design, *Settling*, item 5), so
`assistant_finish_message` now nulls `query_embedding` and `query_embedding_model` where those
columns exist.

**The vector columns are not here.** `setup`'s `provision_vector()` creates the extension and
adds `knowledge_chunks.embedding`, `assistant_steps.query_embedding` and
`query_embedding_model` (design, *Extension*; D62), so an install that cannot create `vector`
loses only meaning search (AC43). The function therefore tests for the columns at run time,
and writes them through dynamic SQL, which compiles only when they exist.

**Affected store and schema.** Postgres: eleven nullable columns and three checks on
`assistant_settings`; `ai_runs`' purpose check replaced; one function replaced, with its
signature, owner and grants kept.

**Compatibility and backfill.** None: every new column is null until an admin sets it; the
purpose check only widens, replaced `NOT VALID` then validated, so `ai_runs` is not locked
against writes.

**Rollback and recovery.** Roll forward only: `sealed_embedding_key` is an organization's
provider key, found nowhere else. Going back past 0078 is a restore.

**Tenant, retention, and security impact.** The columns belong to the organization's own
settings row and go with it (`DELETED_DIRECTLY`). The key is ciphertext under `AI_SECRET_KEY`,
purpose `assistant-embedding-key`, bound to the organization and a hash of the address, and
joins `keys.SEALED_COLUMNS`; the API never returns it. The AI account reads these columns
through its existing `SELECT` on `assistant_settings`, and records a check's result through
three column grants in `insight_account.INSIGHT_GRANTS`.

Revision ID: 0078
Revises: 0077
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP = sa.DateTime(timezone=True)

# Frozen copies, held equal by `test_ai_vocabulary.py`: `nexovue_platform.ai_records.
# RUN_PURPOSES`, the wire formats that can embed (`nexovue_platform.ai.embed.EMBED_FORMATS`),
# and `nexovue_platform.ai.jobs.EMBED_CHECK_REASONS`.
_RUN_PURPOSES = "'explain', 'check', 'chat', 'fix', 'embed'"
_EMBED_WIRE_FORMATS = "'openai_chat', 'bedrock_converse'"
_EMBED_CHECK_REASONS = (
    "'no_connection', 'not_allowed', 'key_unreadable', 'address_refused', 'timed_out', "
    "'unreachable', 'bad_key', 'not_found', 'bad_request', 'limited', 'unavailable', "
    "'refused', 'too_long', 'invalid', 'no_embeddings'"
)
# F3's limit on a stored model name (`nexovue_platform.ai.connection.MODEL_PATTERN`).
_MAX_MODEL_LENGTH = 200

_COLUMNS: tuple[sa.Column[Any], ...] = (
    sa.Column("embedding_model", sa.Text(), nullable=True),
    # The second connection, for embeddings only (D61): all of these, or none.
    sa.Column("embedding_wire_format", sa.Text(), nullable=True),
    sa.Column("embedding_base_url", sa.Text(), nullable=True),
    sa.Column("sealed_embedding_key", sa.LargeBinary(), nullable=True),
    sa.Column("embedding_key_id", postgresql.UUID(as_uuid=True), nullable=True),
    # An owner's agreement to send text to the second connection: its consent fingerprint.
    sa.Column("embedding_consented_fingerprint", sa.Text(), nullable=True),
    sa.Column("embedding_consented_at", _TIMESTAMP, nullable=True),
    # The last embedding check: what it covered, when, and why it failed (null when it passed).
    sa.Column("embedding_checked_fingerprint", sa.Text(), nullable=True),
    sa.Column("embedding_checked_at", _TIMESTAMP, nullable=True),
    sa.Column("embedding_checked_ok", sa.Boolean(), nullable=True),
    sa.Column("embedding_check_reason", sa.Text(), nullable=True),
)

# The function as 0073 made it, with step 5 of the design added: a question's vector goes
# when the question ends, whichever path ends it.
_FINAL_STATES = (
    "'answered', 'partial', 'abstained', 'withheld', 'refused', 'over_limit', 'failed', "
    "'stopped', 'unavailable'"
)
_IN_FLIGHT = "'queued', 'running'"
_FINISH = f"""
CREATE OR REPLACE FUNCTION assistant_finish_message(
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
    -- 5. The question's own vectors, where `setup` added the columns (F4 Step 6). Dynamic,
    -- so the function compiles and runs on an install without the extension.
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.assistant_steps'::regclass
          AND attname = 'query_embedding' AND NOT attisdropped
    ) THEN
        EXECUTE 'UPDATE public.assistant_steps SET query_embedding = NULL, '
            'query_embedding_model = NULL WHERE message_id = $1 '
            'AND (query_embedding IS NOT NULL OR query_embedding_model IS NOT NULL)'
        USING for_message;
    END IF;
    RETURN held.state IN ({_IN_FLIGHT});
END
$$
"""


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("assistant_settings", column)
    op.create_check_constraint(
        "embedding_model_is_bounded",
        "assistant_settings",
        f"embedding_model IS NULL OR length(embedding_model) BETWEEN 1 AND {_MAX_MODEL_LENGTH}",
    )
    op.create_check_constraint(
        "embedding_connection_is_whole",
        "assistant_settings",
        "(embedding_base_url IS NULL) = (embedding_wire_format IS NULL) "
        "AND (sealed_embedding_key IS NULL OR embedding_base_url IS NOT NULL) "
        f"AND (embedding_wire_format IS NULL OR embedding_wire_format IN ({_EMBED_WIRE_FORMATS}))",
    )
    op.create_check_constraint(
        "embedding_check_reason_is_known",
        "assistant_settings",
        f"embedding_check_reason IS NULL OR embedding_check_reason IN ({_EMBED_CHECK_REASONS})",
    )
    op.execute("ALTER TABLE ai_runs DROP CONSTRAINT ck_ai_runs_purpose_is_known")
    op.execute(
        f"ALTER TABLE ai_runs ADD CONSTRAINT ck_ai_runs_purpose_is_known "
        f"CHECK (purpose IN ({_RUN_PURPOSES})) NOT VALID"
    )
    op.execute("ALTER TABLE ai_runs VALIDATE CONSTRAINT ck_ai_runs_purpose_is_known")
    op.execute(_FINISH)


def downgrade() -> None:
    raise RuntimeError(
        "0078 is roll forward only: it holds an organization's sealed embedding key, found "
        "nowhere else. Going back past it is a restore."
    )
