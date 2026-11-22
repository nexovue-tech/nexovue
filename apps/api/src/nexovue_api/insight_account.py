"""The AI role's own Postgres account, `nexovue_insight` (F3, design *The Postgres account*).

`setup` runs `provision` after every migration, and `nexovue setup grants` runs it alone. A
restore drops every grant (`restore-postgres.sh --no-privileges`), and `nexovue restore`
ends in `start`, which runs `setup`, so the grants come back without a step of their own.

What the account may do is `INSIGHT_GRANTS`, and nothing else: everything it held is taken
away first, so a grant removed here is removed from a running install too. It holds no
right on `jobs` or `job_results`. Postgres lets anyone holding `UPDATE`, `DELETE`,
`TRUNCATE` or `MAINTAIN` on a table `LOCK` it, so the alert queue is reached only through
the four functions migration 0070 created, each limited to the `insight` queue.

How an account is made, what is taken from `PUBLIC`, and what is not bounded are
`role_accounts`'s, shared with `connections_account` (F22 T8.7). For this account a refused
revoke from `PUBLIC` leaves it ready, because AI is optional and nothing else depends on this
step (AC71).
"""

from __future__ import annotations

import os
from typing import Final

from nexovue_platform.knowledge import VECTOR_COLUMNS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from nexovue_api import role_accounts
from nexovue_api.role_accounts import RoleAccount, Say, function_grants

from nexovue_api import roles

INSIGHT_ACCOUNT: Final = "nexovue_insight"
PASSWORD_VARIABLE: Final = "POSTGRES_INSIGHT_PASSWORD"
# What the AI role may hold at its worst (A58): a child's pool is one connection and one
# overflow (`process_engine.py`); the parent's claim pool is two and two, with one listener
# (`__main__.py`). The spare is for a restart's overlap. The children are the role's own
# `--concurrency` (`roles.py`), so a role that grows raises the limit with it: F5's eight made
# the old fixed 20 one short.
CHILD_CONNECTIONS: Final = 2
PARENT_CONNECTIONS: Final = 5
RESTART_OVERLAP: Final = 3


def _insight_children() -> int:
    arguments = roles.ROLES["scheduler-insight"]
    return int(arguments[arguments.index("--concurrency") + 1])


CONNECTION_LIMIT: Final = (
    _insight_children() * CHILD_CONNECTIONS + PARENT_CONNECTIONS + RESTART_OVERLAP
)
# The account's own statements are short reads and single-row writes; the provider call
# happens outside any transaction (design, *The wording decision and run*). Guesses.
STATEMENT_TIMEOUT: Final = "10s"
IDLE_IN_TRANSACTION_TIMEOUT: Final = "30s"

# The functions of migrations 0070 and 0073, by signature. F4 (0073) gave the claim a queue
# list, for `assistant` beside `insight`, and added chat's two settling functions; F5 (0077)
# widened the three job functions to `insight-fix`.
INSIGHT_FUNCTIONS: Final = (
    "insight_claim_jobs(text, text[], integer, integer)",
    "insight_retry_job(uuid, text, double precision, text)",
    "insight_finish_job(uuid, text)",
    "insight_store_check_result(uuid, uuid, jsonb, double precision)",
    "assistant_finish_message(uuid, text, jsonb, text, text, text, text)",
    "assistant_record_spend(uuid, bigint, boolean)",
    # F5 Step 4 (D75): the only way a suggestion goes back to `gathering`, when the model asks
    # for files. Migration 0079 created it and took `EXECUTE` from `PUBLIC`.
    "fix_request_more_files(uuid, uuid)",
    # ADR-091 (migration 0081): the pack deleted before the state moves, never by a table
    # grant, which would let this account lock the table.
    "fix_drop_pack(uuid, uuid)",
)

# What the account may do (design, *The Postgres account*). Column lists, never a table
# grant of UPDATE or DELETE. `test_insight_grants_live.py` reads the catalog back.
INSIGHT_GRANTS: Final = (
    # Bound by `org_id` in every statement. All the facts it may see is `model_input`;
    # the real names (`aliases`) and the evidence stay out of reach (AC45).
    "GRANT SELECT (id, org_id, project_id, finding_id, version, model_input, wording_state, "
    "wording_generation, wording_changed_at) ON finding_explanations TO nexovue_insight",
    "GRANT UPDATE (wording, wording_state, wording_reason, wording_changed_at, "
    "wording_from_version) ON finding_explanations TO nexovue_insight",
    # Everything but who consented, when, and the prices; it may record a test's result. The
    # sealed key, which it opens with `AI_SECRET_KEY` for the address it is bound to.
    "GRANT SELECT (id, org_id, wire_format, base_url, model, region_label, key_id, sealed_key, "
    "call_seconds, monthly_token_limit, enabled, off_reason, checked_at, checked_ok, "
    "checked_fingerprint, consented_base_url, consented_fingerprint, consented_schema) "
    "ON ai_connections TO nexovue_insight",
    "GRANT UPDATE (checked_at, checked_ok, checked_fingerprint) "
    "ON ai_connections TO nexovue_insight",
    "GRANT SELECT ON ai_project_opt_outs TO nexovue_insight",
    # So a run can re-check that its project still exists before it spends, bound by
    # organization like every other statement. F4: the name and zone a question's context
    # states, and whose organization it is.
    "GRANT SELECT (id, org_id, name, timezone, deleted_at) ON projects TO nexovue_insight",
    "GRANT SELECT (id, deleted_at) ON organizations TO nexovue_insight",
    # A run's address and purpose stay as written; only its ending may change (AC72).
    "GRANT SELECT, INSERT ON ai_runs TO nexovue_insight",
    "GRANT UPDATE (input_tokens, output_tokens, tokens_estimated, outcome, finished_at) "
    "ON ai_runs TO nexovue_insight",
    "GRANT SELECT, INSERT ON ai_usage_monthly TO nexovue_insight",
    "GRANT UPDATE (used_tokens, reserved_tokens) ON ai_usage_monthly TO nexovue_insight",
    # F4 (design, *Grants*). Row-level security (migration 0073) shows a message only while it
    # is in flight or holds a reservation, and a step only through such a message. Never
    # `ticket_hash`, `answer` or `wrong_reason`; a final write only through the function.
    "GRANT SELECT (id, org_id, project_id, conversation_id, user_id, question, context, "
    "history, data_level, state, phase, failure_reason, rejection_reason, abstain_reason, "
    "reserved_tokens, reserved_month, used_tokens, tokens_estimated, sealed_ticket, "
    "call_seconds, queue_expires_at, deadline_at, hard_ends_at, created_at, finished_at) "
    "ON assistant_messages TO nexovue_insight",
    "GRANT UPDATE (state, phase, reserved_tokens, reserved_month) "
    "ON assistant_messages TO nexovue_insight",
    "GRANT SELECT, INSERT ON assistant_steps TO nexovue_insight",
    "GRANT UPDATE (state, row_count, was_cut, failure, finished_at) "
    "ON assistant_steps TO nexovue_insight",
    "GRANT INSERT ON assistant_read_log TO nexovue_insight",
    # An upsert needs SELECT on what it reads and its conflict columns.
    "GRANT SELECT (org_id, month, kind, count), INSERT ON assistant_monthly_counts "
    "TO nexovue_insight",
    "GRANT UPDATE (count) ON assistant_monthly_counts TO nexovue_insight",
    "GRANT SELECT (id, org_id, project_id) ON assistant_conversations TO nexovue_insight",
    "GRANT SELECT ON assistant_settings TO nexovue_insight",
    # F4 Step 6: an embedding check's result, only on the values it tested (AC85).
    "GRANT UPDATE (embedding_checked_fingerprint, embedding_checked_at, embedding_checked_ok, "
    "embedding_check_reason) ON assistant_settings TO nexovue_insight",
    # F4 Step 3: each windowed tool's retention, so an aged-out range is decided in code
    # (design, *The aged-out check*); `org_id` too, since every statement binds it.
    "GRANT SELECT (project_id, org_id, raw_retention_days, derived_retention_days) "
    "ON project_retention TO nexovue_insight",
    # F5 Step 2b (design, *The Postgres account*): a general suggestion's ids, language,
    # version and retry code, and its token count, which each attempt adds to; it writes the
    # end. Row security (migration 0077) shows a row in rung 2b's states and lets one change
    # only while `proposing`. Never the requester, never the stored answer.
    # Step 4 (T4.2): of a source row, the repository, folder and round it reads by, and the
    # edits, withheld references and asked-for names it writes. Never `files_read`, `edits`
    # back, a pull request column, or who asked.
    "GRANT SELECT (id, org_id, project_id, finding_id, kind, general_language, "
    "explanation_version, state, retry_code, tokens_used, connection_repository_id, folder, "
    "round) ON finding_fixes TO nexovue_insight",
    "GRANT UPDATE (state, state_changed_at, reason, retry_code, general_suggestion, "
    "evidence_ids, tokens_used, finished_at, edits, withheld_refs, requested_paths) "
    "ON finding_fixes TO nexovue_insight",
    # The files sent, while their suggestion is `proposing` (row security, migration 0079);
    # deleted only through `fix_drop_pack` (ADR-091), before the state moves.
    "GRANT SELECT (fix_id, org_id, files, frames, hint) "
    "ON finding_fix_source_packs TO nexovue_insight",
    # FR8, compared before each call: what an owner agreed to, never who or when.
    "GRANT SELECT (org_id, connection_repository_id, provider_base_url, provider_format, "
    "key_id, model, folder_scope, extra_excluded_paths, withdrawn_at) "
    "ON fix_source_consents TO nexovue_insight",
    *function_grants("nexovue_insight", INSIGHT_FUNCTIONS),
)

# F4 Step 6 (design, *Grants*; status, open decision 21): only where `setup`'s
# `provision_vector()` added the columns, since a grant on a missing column fails and an
# install without `vector` must lose only meaning search (AC43). A chunk's text and whose it
# is, to embed it; never its vector, which no statement of the account reads. A question's
# own vector is written on its step, and nulled by `assistant_finish_message`.
VECTOR_GRANTS: Final = (
    "GRANT SELECT (id, org_id, project_id, content, embedded_model, embedding_due) "
    "ON knowledge_chunks TO nexovue_insight",
    "GRANT UPDATE (embedding, embedded_model, embedding_due) "
    "ON knowledge_chunks TO nexovue_insight",
    "GRANT UPDATE (query_embedding, query_embedding_model) ON assistant_steps TO nexovue_insight",
)
# Whether every column `provision_vector()` adds is there; the names are constants.
_VECTOR_PAIRS: Final = ", ".join(f"('{table}', '{column}')" for table, column, _ in VECTOR_COLUMNS)
VECTOR_COLUMNS_PRESENT: Final = text(
    f"""
    SELECT count(*) = {len(VECTOR_COLUMNS)} FROM pg_catalog.pg_attribute AS a
    JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND NOT a.attisdropped
      AND (c.relname::text, a.attname::text) IN ({_VECTOR_PAIRS})
    """
)

INSIGHT: Final = RoleAccount(
    name=INSIGHT_ACCOUNT,
    password_variable=PASSWORD_VARIABLE,
    connection_limit=CONNECTION_LIMIT,
    statement_timeout=STATEMENT_TIMEOUT,
    idle_in_transaction_timeout=IDLE_IN_TRANSACTION_TIMEOUT,
    functions=INSIGHT_FUNCTIONS,
    grants=INSIGHT_GRANTS,
    label="ai account",
    service="scheduler-insight",
    when_missing="nothing else is affected",
    process="AI process",
    guide="docs/operations/ai.md",
)
# Read by `test_insight_grants_live.py`, which probes another account with it.
_HELD_TOO_MUCH: Final = role_accounts.HELD_TOO_MUCH


async def provision(dsn: str | None = None, *, say: Say = print) -> bool:
    """Create or update the account and apply its grants. True when the account is ready.

    Never raises for a refusal: AI is optional, so a missing password or a refused right
    is said plainly and `setup` goes on (AC71). A refused revoke from `PUBLIC` still leaves
    the account ready, with its remaining risk said (AC43)."""
    password = os.environ.get(PASSWORD_VARIABLE, "")
    if not password:
        say(
            f"ai account: {PASSWORD_VARIABLE} is not set, so {INSIGHT_ACCOUNT} was not "
            "created; scheduler-insight cannot start, and nothing else is affected"
        )
        return False
    engine = create_async_engine(dsn or os.environ["POSTGRES_DSN"], pool_size=1, max_overflow=0)
    try:
        if not await role_accounts.prepare_account(
            engine, INSIGHT, password, say, apply_grants=_apply_grants
        ):
            return False
        await role_accounts.take_shared_rights(engine, INSIGHT, say)
        return True
    finally:
        await engine.dispose()


async def _apply_grants(connection: AsyncConnection) -> None:
    """The listed grants, then meaning search's where `setup` added its columns."""
    await role_accounts.apply_listed_grants(connection, INSIGHT)
    if await vector_columns_present(connection):
        for statement in VECTOR_GRANTS:
            await connection.exec_driver_sql(statement)


async def vector_columns_present(connection: AsyncConnection | AsyncSession) -> bool:
    """Whether `setup` added meaning search's columns (F4 Step 6; AC43)."""
    return bool(await connection.scalar(VECTOR_COLUMNS_PRESENT))


async def account_exists(session: AsyncSession) -> bool:
    """Whether setup has made the AI account on this server (F3, AC71)."""
    return await role_accounts.account_exists(session, INSIGHT)


async def beat_lock_exposed(session: AsyncSession) -> bool:
    """Whether the AI account may still take beat's leader lock. Ask only once
    `account_exists` holds."""
    return await role_accounts.beat_lock_exposed(session, INSIGHT)
