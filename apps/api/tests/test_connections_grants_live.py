"""The tracker account holds exactly its list, read back from the catalog (F22 plan T8.7).

The list below is written out, not taken from `connections_account.CONNECTIONS_GRANTS`: a
check that read the code under test would agree with it when both were wrong. Every table and
column in `public` is asked about with Postgres's own `has_*_privilege`, which counts what
`PUBLIC` holds too, so a right reaching the account any way at all shows here.

That the list is **enough** is proved elsewhere: the connections suites run their jobs as this
account (`connections_engine`), and `test_connections_end_to_end_live.py` runs the real
`scheduler-connections` worker signed in as it.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from connections_login import CONNECTIONS_ACCOUNT, DEV_CONNECTIONS_PASSWORD, connections_dsn
from insight_login import as_account
from live import require_stack
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nexovue_api import connections_account, role_accounts

DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
CONNECTIONS_ASYNC_DSN: Final = connections_dsn(ASYNC_DSN)

TABLE_PRIVILEGES: Final = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
COLUMN_PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "REFERENCES")

# Table-level rights: only `DELETE`, on F5's three short-lived tables whose rows it ends.
EXPECTED_TABLE_RIGHTS: Final = {
    ("finding_fix_source_packs", "DELETE"),
    ("finding_fix_checks", "DELETE"),
    ("github_link_attempts", "DELETE"),
}
EXPECTED_COLUMN_RIGHTS: Final = {
    # --- F22 ---
    ("connections", "SELECT"): {
        "id",
        "org_id",
        "kind",
        "state",
        "sealed_credential",
        "sealed_manifest_code",
        "setup_expires_at",
        "github_app_id",
        "github_app_slug",
        "github_installation_id",
        "sync_lease_until",
        "paused_until",
        "last_ok_at",
        "failing_since",
        "consecutive_failures",
        "last_failure",
        "last_failure_status",
        "failure_told_at",
        "repositories_read_at",
        "github_client_id",
        "sealed_client_secret",
        "granted_permissions",
    },
    ("connections", "UPDATE"): {
        "state",
        "sealed_credential",
        "sealed_webhook_secret",
        "sealed_manifest_code",
        "github_app_id",
        "github_app_slug",
        "github_installation_id",
        "sync_lease_until",
        "paused_until",
        "last_ok_at",
        "failing_since",
        "consecutive_failures",
        "last_failure",
        "last_failure_status",
        "failure_told_at",
        "repositories_read_at",
        "github_client_id",
        "sealed_client_secret",
        "granted_permissions",
        "granted_at",
    },
    ("connection_repositories", "SELECT"): {
        "id",
        "org_id",
        "connection_id",
        "external_id",
        "key",
        "is_private",
        "is_reachable",
        "unreachable_since",
        "public_told_at",
        "public_allowed_at",
        "public_allowed_by",
    },
    ("connection_repositories", "INSERT"): {
        "org_id",
        "connection_id",
        "external_id",
        "key",
        "is_private",
        "is_reachable",
        "public_told_at",
    },
    ("connection_repositories", "UPDATE"): {
        "key",
        "is_private",
        "is_reachable",
        "unreachable_since",
        "public_told_at",
        "public_allowed_at",
        "public_allowed_by",
    },
    ("finding_links", "SELECT"): {
        "id",
        "org_id",
        "finding_id",
        "connection_id",
        "place_id",
        "role",
        "sync_status",
        "external_id",
        "external_key",
        "seen_state",
        "wanted_state",
        "comment_wanted",
        "idle_warning_at",
        "tracker_closed_at",
        "frozen_facts",
        "first_attempt_at",
        "next_sync_at",
        "last_synced_at",
        "gone_at",
        "failures",
        "last_failure",
    },
    ("finding_links", "UPDATE"): {
        "place_id",
        "sync_status",
        "external_id",
        "external_key",
        "seen_state",
        "wanted_state",
        "comment_wanted",
        "comment_written_at",
        "tracker_closed_at",
        "frozen_facts",
        "first_attempt_at",
        "next_sync_at",
        "last_synced_at",
        "gone_at",
        "failures",
        "last_failure",
    },
    ("findings", "SELECT"): {
        "id",
        "org_id",
        "project_id",
        "scope",
        "service_name",
        "state",
        "signals",
        "case_state",
        "case_resolved_at",
        "case_activity_at",
        "root_finding_id",
    },
    ("findings", "UPDATE"): {
        "case_state",
        "case_resolution",
        "case_resolved_at",
        "case_resolved_by",
        "case_activity_at",
        "case_warned_at",
        "root_finding_id",
        "updated_at",
    },
    ("finding_events", "SELECT"): {"id"},
    ("finding_events", "INSERT"): {
        "org_id",
        "finding_id",
        "kind",
        "actor_kind",
        "actor_id",
        "detail",
        "at",
    },
    ("finding_emails_owed", "INSERT"): {"org_id", "finding_id", "kind", "at"},
    ("audit_log", "INSERT"): {"org_id", "actor_user_id", "action", "target", "detail"},
    ("users", "SELECT"): {"id", "org_id", "role", "removed_at"},
    # --- F5 ---
    ("finding_explanations", "SELECT"): {
        "org_id",
        "finding_id",
        "version",
        "likely_cause",
        "model_input",
        "aliases",
    },
    ("ai_connections", "SELECT"): {
        "id",
        "org_id",
        "base_url",
        "wire_format",
        "key_id",
        "model",
        "enabled",
        "off_reason",
    },
    ("finding_fixes", "SELECT"): {
        "id",
        "org_id",
        "project_id",
        "finding_id",
        "kind",
        "explanation_version",
        "state",
        "state_changed_at",
        "reason",
        "requested_by",
        "finished_at",
        "result_recorded_at",
        "connection_repository_id",
        "folder",
        "path_candidates",
        "requested_paths",
        "round",
        "candidates_from",
        "read_commit",
        "files_read",
        "checks_version",
        "extra_list_hash",
        "edits",
        "withheld_refs",
        "code_deleted_at",
        "code_deleted_reason",
        "notify_requester",
        "sent_at",
        "approved_by",
        "approved_digest",
        "requester_note",
        "requester_note_confirmed",
        "ci_requested_by",
        "ci_requested_at",
        "pull_request_number",
        "pull_request_branch",
        "pull_request_state",
        "pull_request_commit_sha",
        "ci_commit_sha",
        "last_write_failure",
        "ci_parent_sha",
        "ci_commit_requested_at",
        "ci_pushed_at",
        "pull_request_state_at",
        "pull_request_polled_at",
    },
    ("finding_fixes", "UPDATE"): {
        "state",
        "state_changed_at",
        "reason",
        "finished_at",
        "result_recorded_at",
        "path_candidates",
        "requested_paths",
        "read_commit",
        "read_commit_known",
        "files_read",
        "checks_version",
        "extra_list_hash",
        "files_digest",
        "edits",
        "withheld_refs",
        "code_deleted_at",
        "code_deleted_reason",
        "email_due",
        "requester_note",
        "approved_digest",
        "pull_request_number",
        "pull_request_url",
        "pull_request_branch",
        "pull_request_state",
        "pull_request_commit_sha",
        "ci_commit_sha",
        "last_write_failure",
        "ci_parent_sha",
        "ci_commit_requested_at",
        "ci_pushed_at",
        "pull_request_state_at",
        "pull_request_merge_commit_sha",
        "pull_request_polled_at",
    },
    ("finding_fix_source_packs", "SELECT"): {
        "fix_id",
        "org_id",
        "files",
        "frames",
        "bytes",
        "expires_at",
    },
    ("finding_fix_source_packs", "INSERT"): {
        "fix_id",
        "org_id",
        "files",
        "frames",
        "bytes",
        "expires_at",
    },
    ("finding_fix_source_packs", "UPDATE"): {"files", "frames", "bytes", "expires_at"},
    ("finding_fix_checks", "SELECT"): {
        "id",
        "org_id",
        "fix_id",
        "requested_by",
        "purpose",
        "status",
        "expires_at",
    },
    ("finding_fix_checks", "UPDATE"): {
        "status",
        "reason",
        "head_sha",
        "blob_ids",
        "moved_refs",
        "patch",
        "digest",
        "finished_at",
        "expires_at",
    },
    ("github_link_attempts", "SELECT"): {
        "id",
        "org_id",
        "user_id",
        "status",
        "sealed_code",
        "expires_at",
    },
    ("github_link_attempts", "UPDATE"): {"status", "reason", "sealed_code"},
    ("fix_source_consents", "SELECT"): {
        "id",
        "org_id",
        "connection_repository_id",
        "provider_base_url",
        "provider_format",
        "key_id",
        "model",
        "folder_scope",
        "extra_excluded_paths",
        "allowed_config_paths",
        "require_github_access",
        "withdrawn_at",
    },
    ("fix_source_consents", "UPDATE"): {"withdrawn_at", "withdrawn_reason"},
    ("service_code_locations", "SELECT"): {
        "org_id",
        "project_id",
        "service_name",
        "connection_repository_id",
        "folder",
        "path_in_production",
    },
    ("github_user_links", "SELECT"): {
        "user_id",
        "org_id",
        "github_user_id",
        "github_login",
        "linked_at",
    },
    ("github_user_links", "INSERT"): {
        "user_id",
        "org_id",
        "github_user_id",
        "github_login",
        "linked_at",
    },
    ("github_user_links", "UPDATE"): {"github_user_id", "github_login", "linked_at"},
    ("fix_repository_settings", "SELECT"): {
        "org_id",
        "connection_repository_id",
        "drafts_allowed",
        "ci_on_open",
        "draft_refused_at",
    },
    ("fix_repository_settings", "UPDATE"): {"draft_refused_at"},
    # The sweep's finish (F5 T9.1): its upsert reads the conflict key and `EXCLUDED.finished_at`,
    # so `SELECT` on both (measured: refused with either missing). Never `DELETE`.
    ("fix_sweep_status", "SELECT"): {"sweep", "finished_at"},
    ("fix_sweep_status", "INSERT"): {"sweep", "finished_at"},
    ("fix_sweep_status", "UPDATE"): {"finished_at"},
}
EXPECTED_FUNCTIONS: Final = {
    "connections_claim_jobs(text,text[],integer,integer)",
    "connections_retry_job(uuid,text,double precision,text)",
    "connections_finish_job(uuid,text)",
    "connections_enqueue_job(text,text,jsonb,uuid,double precision,double precision,"
    "integer,integer,integer)",
    "connections_store_test_result(uuid,uuid,jsonb,double precision)",
    "connections_brief_request(uuid,uuid)",
    "connections_answer_brief(uuid,uuid,jsonb)",
}
# What an alert, a person's sign-in or another role's key lives in: never readable here.
NEVER_READ: Final = (
    ("users", "password_hash"),
    ("users", "totp_secret"),
    ("users", "email"),
    ("ai_connections", "sealed_key"),
    ("finding_explanations", "evidence"),
    ("findings", "title"),
    ("connections", "setup_state_hash"),
    ("connections", "sealed_webhook_secret"),
)
NO_RIGHT_AT_ALL: Final = (
    "jobs",
    "job_results",
    "notifications",
    "notification_channels",
    "check_secrets",
    "api_keys",
    "sessions",
    "finding_notes",
    "alert_rules",
)

_TABLES: Final = text(
    "SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') ORDER BY 1"
)
_TABLE_RIGHT: Final = text("SELECT has_table_privilege(:account, :table, :privilege)")
_COLUMNS_WITH_RIGHT: Final = text(
    """
    SELECT attname FROM pg_attribute
    WHERE attrelid = CAST(:table AS regclass) AND attnum > 0 AND NOT attisdropped
      AND has_column_privilege(:account, attrelid, attnum, :privilege)
    """
)
# Functions in `public` that are not an extension's own (pgvector's are plain arithmetic).
_EXECUTABLE: Final = text(
    """
    SELECT replace(p.oid::regprocedure::text, ', ', ',') FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND NOT EXISTS (SELECT 1 FROM pg_depend AS d WHERE d.objid = p.oid AND d.deptype = 'e')
      AND has_function_privilege(:account, p.oid, 'EXECUTE')
    """
)


def _reachable() -> bool:
    parts = urlsplit(DSN)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(),
    suite="the connections account grants suite",
    remedy="start the dev stack so Postgres answers (`just dev`)",
)


@pytest.fixture
def password(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(connections_account.PASSWORD_VARIABLE, DEV_CONNECTIONS_PASSWORD)
    return DEV_CONNECTIONS_PASSWORD


@pytest.fixture
async def engine(password: str) -> AsyncIterator[AsyncEngine]:
    said: list[str] = []
    assert await connections_account.provision(ASYNC_DSN, say=said.append), said
    built = create_async_engine(ASYNC_DSN)
    yield built
    await built.dispose()


@pytest.fixture
async def as_connections(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(CONNECTIONS_ASYNC_DSN, pool_size=1, max_overflow=0)
    yield built
    await built.dispose()


async def _rights(
    engine: AsyncEngine,
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], set[str]]]:
    """Every table right and every column right the account holds, from the catalog."""
    tables: set[tuple[str, str]] = set()
    columns: dict[tuple[str, str], set[str]] = {}
    account = {"account": CONNECTIONS_ACCOUNT}
    async with engine.connect() as connection:
        names = (await connection.execute(_TABLES)).scalars().all()
        for table in names:
            qualified = {**account, "table": f"public.{table}"}
            for privilege in TABLE_PRIVILEGES:
                if await connection.scalar(_TABLE_RIGHT, {**qualified, "privilege": privilege}):
                    tables.add((table, privilege))
            for privilege in COLUMN_PRIVILEGES:
                if (table, privilege) in tables:
                    continue
                found = set(
                    (
                        await connection.execute(
                            _COLUMNS_WITH_RIGHT, {**qualified, "privilege": privilege}
                        )
                    ).scalars()
                )
                if found:
                    columns[(table, privilege)] = found
    return tables, columns


async def test_the_account_holds_exactly_its_list(engine: AsyncEngine) -> None:
    """Column by column, nothing more, no table-level `UPDATE`, and `EXECUTE` on 0085's
    functions alone: none of the AI account's, none of `PUBLIC`'s that reach past a table."""
    tables, columns = await _rights(engine)

    assert tables == EXPECTED_TABLE_RIGHTS
    assert columns == EXPECTED_COLUMN_RIGHTS
    async with engine.connect() as connection:
        functions = set(
            (await connection.execute(_EXECUTABLE, {"account": CONNECTIONS_ACCOUNT})).scalars()
        )
    assert functions == EXPECTED_FUNCTIONS


async def test_the_account_reads_no_secret_alert_or_queue_row(engine: AsyncEngine) -> None:
    """What a broken-into tracker process must not reach: a person's sign-in, another role's
    key, a finding's evidence, the alert queue and its results."""
    async with engine.connect() as connection:
        for table, column in NEVER_READ:
            held = await connection.scalar(
                text("SELECT has_column_privilege(:account, :table, :column, 'SELECT')"),
                {"account": CONNECTIONS_ACCOUNT, "table": table, "column": column},
            )
            assert held is False, (table, column)
        for table in NO_RIGHT_AT_ALL:
            held = await connection.scalar(
                text(
                    "SELECT has_any_column_privilege(:account, :table, "
                    "'SELECT, INSERT, UPDATE, REFERENCES') "
                    "OR has_table_privilege(:account, :table, 'DELETE, TRUNCATE, TRIGGER')"
                ),
                {"account": CONNECTIONS_ACCOUNT, "table": table},
            )
            assert held is False, table


async def test_the_account_cannot_lock_the_alert_queue_or_a_finding(
    as_connections: AsyncEngine,
) -> None:
    """A column grant does not permit `LOCK TABLE` (ADR-040's measurement), and the account
    holds nothing on `jobs`. Each mode is tried in its own transaction."""
    for table in ("jobs", "job_results", "findings", "finding_links", "connections"):
        for mode in ("ACCESS EXCLUSIVE", "ROW EXCLUSIVE", "ACCESS SHARE"):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with as_connections.begin() as connection:
                    await connection.execute(text(f"LOCK TABLE {table} IN {mode} MODE NOWAIT"))


async def test_the_account_takes_no_advisory_lock_and_makes_no_temporary_table(
    as_connections: AsyncEngine,
) -> None:
    """Beat's leader lock is an advisory lock: a broken-into tracker process must not be able
    to stop every schedule by holding it."""
    with pytest.raises(DBAPIError, match="permission denied"):
        async with as_connections.begin() as connection:
            await connection.execute(text("SELECT pg_try_advisory_lock(1)"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with as_connections.begin() as connection:
            await connection.execute(text("CREATE TEMP TABLE stray (x int)"))


async def test_the_account_is_a_plain_login_with_a_limit_and_timeouts(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        role = (
            await connection.execute(
                text(
                    "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolinherit, "
                    "rolreplication, rolbypassrls, rolconnlimit, rolconfig, "
                    "(SELECT count(*) FROM pg_auth_members WHERE member = r.oid) AS memberships, "
                    "has_database_privilege(rolname, current_database(), 'TEMP') AS temp, "
                    "has_database_privilege(rolname, current_database(), 'CREATE') AS create_db, "
                    "has_schema_privilege(rolname, 'public', 'CREATE') AS create_schema "
                    "FROM pg_roles AS r WHERE rolname = :account"
                ),
                {"account": CONNECTIONS_ACCOUNT},
            )
        ).one()

    assert role.rolcanlogin
    assert not (role.rolsuper or role.rolcreaterole or role.rolcreatedb or role.rolinherit)
    assert not (role.rolreplication or role.rolbypassrls)
    assert role.rolconnlimit == connections_account.CONNECTION_LIMIT
    assert set(role.rolconfig) == {
        f"statement_timeout={connections_account.STATEMENT_TIMEOUT}",
        f"idle_in_transaction_session_timeout={connections_account.IDLE_IN_TRANSACTION_TIMEOUT}",
    }
    assert role.memberships == 0
    assert not (role.temp or role.create_db or role.create_schema)


async def test_row_security_admits_this_account_to_every_suggestion(engine: AsyncEngine) -> None:
    """0085's policies: F5's jobs here move a suggestion through every state, so the AI
    account's `proposing`-only policies must not bind this account. That the jobs then work
    on a row in `gathering` is `test_fix_source_pack.py`'s, run as this account."""
    async with engine.connect() as connection:
        policies = {
            (row.tablename, row.policyname): (row.cmd, row.qual, row.with_check)
            for row in await connection.execute(
                text(
                    "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies "
                    "WHERE policyname LIKE '%connections_role'"
                )
            )
        }
    admitted = f"(CURRENT_USER = '{CONNECTIONS_ACCOUNT}'::name)"
    assert policies == {
        ("finding_fixes", "finding_fixes_connections_role"): ("ALL", admitted, admitted),
        ("finding_fix_source_packs", "finding_fix_source_packs_connections_role"): (
            "ALL",
            admitted,
            admitted,
        ),
    }


async def test_a_right_given_by_hand_is_taken_back_by_the_next_setup(
    engine: AsyncEngine, password: str
) -> None:
    """`setup` takes everything away first, so its list is all the account has after it. The
    hand grant takes `setup`'s own lock, so a suite provisioning at the same time cannot
    collide with it on the catalog."""
    async with engine.begin() as connection:
        await role_accounts.serialised(connection)
        await connection.execute(
            text(f"GRANT SELECT, UPDATE ON dashboards TO {CONNECTIONS_ACCOUNT}")
        )

    assert await connections_account.provision(ASYNC_DSN, say=lambda _line: None)

    tables, _ = await _rights(engine)
    assert tables == EXPECTED_TABLE_RIGHTS


async def test_setup_without_the_password_says_so_and_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No account, no failure; only the tracker role cannot start, and cases still work."""
    monkeypatch.delenv(connections_account.PASSWORD_VARIABLE, raising=False)
    said: list[str] = []

    assert await connections_account.provision(ASYNC_DSN, say=said.append) is False
    assert said and "POSTGRES_CONNECTIONS_PASSWORD is not set" in said[0]


async def test_setup_under_an_account_that_cannot_manage_roles_warns_and_goes_on(
    engine: AsyncEngine, password: str
) -> None:
    """A managed Postgres's account with no `CREATEROLE` is refused, and `setup` says what to
    do, without the password in what it says."""
    probe = f"connections_setup_probe_{uuid.uuid4().hex[:12]}"
    async with engine.begin() as connection:
        await role_accounts.serialised(connection)
        await connection.execute(text(f"CREATE ROLE {probe} LOGIN PASSWORD 'probe'"))
        await connection.execute(
            text(f"GRANT EXECUTE ON FUNCTION pg_advisory_xact_lock(bigint) TO {probe}")
        )
    said: list[str] = []
    try:
        probe_dsn = as_account(DSN, probe, "probe").replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
        ready = await connections_account.provision(probe_dsn, say=said.append)
    finally:
        async with engine.begin() as connection:
            # Taking back the probe's right on the lock function rewrites the function's row.
            await role_accounts.serialised(connection)
            await connection.execute(text(f"DROP OWNED BY {probe}"))
            await connection.execute(text(f"DROP ROLE {probe}"))

    assert ready is False
    assert len(said) == 1 and "refused to prepare nexovue_connections" in said[0]
    assert "permission denied" in said[0]
    assert password not in said[0]


async def test_setup_will_not_call_an_over_privileged_account_ready(
    password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(role_accounts, "HELD_TOO_MUCH", text("SELECT true OR :account = ''"))
    said: list[str] = []

    assert await connections_account.provision(ASYNC_DSN, say=said.append) is False
    assert "holds a right no grant can fence" in said[-1]
