"""The tracker role's own Postgres account, `nexovue_connections` (F22 plan T8.7; ADR-030).

`scheduler-connections` opens tracker credentials and parses answers from the internet, so it
signs in as an account of its own, made by F3's pattern (`role_accounts`, ADR-040): `setup`
runs `provision` after every migration and `nexovue setup grants` runs it alone, so a restore,
which drops every grant, ends with them back.

**What it may do is `CONNECTIONS_GRANTS`, read from the statements the connections task set
runs** (`registry.TASK_SETS["connections"]`: F22's sync, refresh, test and sweep, and F5's
pack, check, draft, link, poller and sweep). Column lists wherever a table is shared with
another feature; a table-level right only on F5's own short-lived tables, whose rows it
deletes. **No right on `jobs` or `job_results`:** migration 0085's seven functions, limited to
the two tracker queues and the `connection_test` and `brief_paths` results, take their place.
Row security on `finding_fixes` and its packs admits this account by 0085's two policies.
`test_connections_grants_live.py` reads the catalog back against a list written out by hand.

**What a broken-into process of it can still do,** stated plainly: read and change every
organization's connections, links and case columns, and F5's suggestions, because the jobs
cross organizations by nature and no grant can bind `org_id`; read the explanation facts F5
sends (`model_input`, `aliases`); and set any time as when F5's sweep last finished, keeping
its stall alert quiet (a function writing `now()` would not stop that: the process could call
it without sweeping). It cannot read a check, channel, AI or mail secret, an alert,
telemetry or a person's sign-in, lock the alert queue, or add a job outside its queues.

A missing password stops this role alone, which cannot sign in, and `setup` goes on.
"""

from __future__ import annotations

import os
from typing import Final

from sqlalchemy.ext.asyncio import create_async_engine

from nexovue_api import role_accounts
from nexovue_api.role_accounts import RoleAccount, Say, function_grants

CONNECTIONS_ACCOUNT: Final = "nexovue_connections"
PASSWORD_VARIABLE: Final = "POSTGRES_CONNECTIONS_PASSWORD"
# Three children, each with its own small pool, the parent's pool and its listener need well
# under this. A guess, as the AI account's is.
CONNECTION_LIMIT: Final = 20
# Its statements are short; no lock is held across a call to a tracker (`sync.py`). Guesses,
# the AI account's values.
STATEMENT_TIMEOUT: Final = "10s"
IDLE_IN_TRANSACTION_TIMEOUT: Final = "30s"

# Migration 0085's functions, by signature.
CONNECTIONS_FUNCTIONS: Final = (
    "connections_claim_jobs(text, text[], integer, integer)",
    "connections_retry_job(uuid, text, double precision, text)",
    "connections_finish_job(uuid, text)",
    "connections_enqueue_job(text, text, jsonb, uuid, double precision, double precision, "
    "integer, integer, integer)",
    "connections_store_test_result(uuid, uuid, jsonb, double precision)",
    "connections_brief_request(uuid, uuid)",
    "connections_answer_brief(uuid, uuid, jsonb)",
)

_TO: Final = f"TO {CONNECTIONS_ACCOUNT}"

CONNECTIONS_GRANTS: Final = (
    # --- F22: the connection, what it reaches, and its links -------------------------------
    # Everything the jobs load and move; never who set it up, its setup state's hash, Jira's
    # site or when it was made. The webhook secret is written sealed, never read back.
    "GRANT SELECT (id, org_id, kind, state, sealed_credential, sealed_manifest_code, "
    "setup_expires_at, github_app_id, github_app_slug, github_installation_id, "
    "sync_lease_until, paused_until, last_ok_at, failing_since, consecutive_failures, "
    "last_failure, last_failure_status, failure_told_at, repositories_read_at, "
    f"github_client_id, sealed_client_secret, granted_permissions) ON connections {_TO}",
    "GRANT UPDATE (state, sealed_credential, sealed_webhook_secret, sealed_manifest_code, "
    "github_app_id, github_app_slug, github_installation_id, sync_lease_until, paused_until, "
    "last_ok_at, failing_since, consecutive_failures, last_failure, last_failure_status, "
    "failure_told_at, repositories_read_at, github_client_id, sealed_client_secret, "
    f"granted_permissions, granted_at) ON connections {_TO}",
    # The refresh's upsert (FR42): an admin's allowance is only ever cleared, never given.
    "GRANT SELECT (id, org_id, connection_id, external_id, key, is_private, is_reachable, "
    "unreachable_since, public_told_at, public_allowed_at, public_allowed_by) "
    f"ON connection_repositories {_TO}",
    "GRANT INSERT (org_id, connection_id, external_id, key, is_private, is_reachable, "
    f"public_told_at) ON connection_repositories {_TO}",
    "GRANT UPDATE (key, is_private, is_reachable, unreachable_since, public_told_at, "
    f"public_allowed_at, public_allowed_by) ON connection_repositories {_TO}",
    # A link is made and removed by a person, through the API; the sync only moves it.
    "GRANT SELECT (id, org_id, finding_id, connection_id, place_id, role, sync_status, "
    "external_id, external_key, seen_state, wanted_state, comment_wanted, idle_warning_at, "
    "tracker_closed_at, frozen_facts, first_attempt_at, next_sync_at, last_synced_at, gone_at, "
    f"failures, last_failure) ON finding_links {_TO}",
    "GRANT UPDATE (place_id, sync_status, external_id, external_key, seen_state, wanted_state, "
    "comment_wanted, comment_written_at, tracker_closed_at, frozen_facts, first_attempt_at, "
    f"next_sync_at, last_synced_at, gone_at, failures, last_failure) ON finding_links {_TO}",
    # --- the case columns a tracker's change moves (D73), and the records it leaves ----------
    # Never a finding's evidence, title, severity, assignee or feedback.
    "GRANT SELECT (id, org_id, project_id, scope, service_name, state, signals, case_state, "
    f"case_resolved_at, case_activity_at, root_finding_id) ON findings {_TO}",
    "GRANT UPDATE (case_state, case_resolution, case_resolved_at, case_resolved_by, "
    f"case_activity_at, case_warned_at, root_finding_id, updated_at) ON findings {_TO}",
    # `id` for the event's `RETURNING`; events, owed emails and audit rows are only added.
    "GRANT INSERT (org_id, finding_id, kind, actor_kind, actor_id, detail, at) "
    f"ON finding_events {_TO}",
    f"GRANT SELECT (id) ON finding_events {_TO}",
    f"GRANT INSERT (org_id, finding_id, kind, at) ON finding_emails_owed {_TO}",
    f"GRANT INSERT (org_id, actor_user_id, action, target, detail) ON audit_log {_TO}",
    # Whether the person behind a check or a draft is still a member in a role that may.
    f"GRANT SELECT (id, org_id, role, removed_at) ON users {_TO}",
    # --- F5: reading files, checks, drafts, the account link, its poller and sweep ----------
    # The facts a check names and the cause a draft states (F5 design, *Grants*).
    "GRANT SELECT (org_id, finding_id, version, likely_cause, model_input, aliases) "
    f"ON finding_explanations {_TO}",
    # Whether AI is on, and the provider a consent was given for (FR8); never the sealed key.
    "GRANT SELECT (id, org_id, base_url, wire_format, key_id, model, enabled, off_reason) "
    f"ON ai_connections {_TO}",
    # Never the general suggestion, its evidence, its tokens, the verdict or who sent it.
    "GRANT SELECT (id, org_id, project_id, finding_id, kind, explanation_version, state, "
    "state_changed_at, reason, requested_by, finished_at, result_recorded_at, "
    "connection_repository_id, folder, path_candidates, requested_paths, round, "
    "candidates_from, read_commit, files_read, checks_version, extra_list_hash, edits, "
    "withheld_refs, code_deleted_at, code_deleted_reason, notify_requester, sent_at, "
    "approved_by, approved_digest, requester_note, requester_note_confirmed, ci_requested_by, "
    "ci_requested_at, pull_request_number, pull_request_branch, pull_request_state, "
    "pull_request_commit_sha, ci_commit_sha, last_write_failure, ci_parent_sha, "
    "ci_commit_requested_at, ci_pushed_at, pull_request_state_at, pull_request_polled_at) "
    f"ON finding_fixes {_TO}",
    "GRANT UPDATE (state, state_changed_at, reason, finished_at, result_recorded_at, "
    "path_candidates, requested_paths, read_commit, read_commit_known, files_read, "
    "checks_version, extra_list_hash, files_digest, edits, withheld_refs, code_deleted_at, "
    "code_deleted_reason, email_due, requester_note, approved_digest, pull_request_number, "
    "pull_request_url, pull_request_branch, pull_request_state, pull_request_commit_sha, "
    "ci_commit_sha, last_write_failure, ci_parent_sha, ci_commit_requested_at, ci_pushed_at, "
    f"pull_request_state_at, pull_request_merge_commit_sha, pull_request_polled_at) "
    f"ON finding_fixes {_TO}",
    # F5's own short-lived tables: the pack it writes and deletes, each check's patch, and a
    # link attempt's sealed code. `DELETE` lets it lock these, and nothing an alert reads.
    # `bytes` too, and the link's login and time below: an upsert's `EXCLUDED.x` needs `SELECT`
    # on `x` (measured: refused without it).
    "GRANT SELECT (fix_id, org_id, files, frames, bytes, expires_at) "
    f"ON finding_fix_source_packs {_TO}",
    "GRANT INSERT (fix_id, org_id, files, frames, bytes, expires_at) "
    f"ON finding_fix_source_packs {_TO}",
    f"GRANT UPDATE (files, frames, bytes, expires_at) ON finding_fix_source_packs {_TO}",
    f"GRANT DELETE ON finding_fix_source_packs {_TO}",
    "GRANT SELECT (id, org_id, fix_id, requested_by, purpose, status, expires_at) "
    f"ON finding_fix_checks {_TO}",
    "GRANT UPDATE (status, reason, head_sha, blob_ids, moved_refs, patch, digest, finished_at, "
    f"expires_at) ON finding_fix_checks {_TO}",
    f"GRANT DELETE ON finding_fix_checks {_TO}",
    "GRANT SELECT (id, org_id, user_id, status, sealed_code, expires_at) "
    f"ON github_link_attempts {_TO}",
    f"GRANT UPDATE (status, reason, sealed_code) ON github_link_attempts {_TO}",
    f"GRANT DELETE ON github_link_attempts {_TO}",
    # What an owner agreed to (FR8), and only its withdrawal when access is gone.
    "GRANT SELECT (id, org_id, connection_repository_id, provider_base_url, provider_format, "
    "key_id, model, folder_scope, extra_excluded_paths, allowed_config_paths, "
    f"require_github_access, withdrawn_at) ON fix_source_consents {_TO}",
    f"GRANT UPDATE (withdrawn_at, withdrawn_reason) ON fix_source_consents {_TO}",
    "GRANT SELECT (org_id, project_id, service_name, connection_repository_id, folder, "
    f"path_in_production) ON service_code_locations {_TO}",
    "GRANT SELECT (user_id, org_id, github_user_id, github_login, linked_at) "
    f"ON github_user_links {_TO}",
    "GRANT INSERT (user_id, org_id, github_user_id, github_login, linked_at) "
    f"ON github_user_links {_TO}",
    f"GRANT UPDATE (github_user_id, github_login, linked_at) ON github_user_links {_TO}",
    "GRANT SELECT (org_id, connection_repository_id, drafts_allowed, ci_on_open, "
    f"draft_refused_at) ON fix_repository_settings {_TO}",
    f"GRANT UPDATE (draft_refused_at) ON fix_repository_settings {_TO}",
    # When the sweep last finished a whole pass (migration 0086), which beat reads for the
    # stall alert. The upsert reads its key and `EXCLUDED.finished_at`, so `SELECT` on both
    # (measured: refused with either missing); the key is never changed or a row deleted.
    f"GRANT SELECT (sweep, finished_at) ON fix_sweep_status {_TO}",
    f"GRANT INSERT (sweep, finished_at) ON fix_sweep_status {_TO}",
    f"GRANT UPDATE (finished_at) ON fix_sweep_status {_TO}",
    *function_grants(CONNECTIONS_ACCOUNT, CONNECTIONS_FUNCTIONS),
)

CONNECTIONS: Final = RoleAccount(
    name=CONNECTIONS_ACCOUNT,
    password_variable=PASSWORD_VARIABLE,
    connection_limit=CONNECTION_LIMIT,
    statement_timeout=STATEMENT_TIMEOUT,
    idle_in_transaction_timeout=IDLE_IN_TRANSACTION_TIMEOUT,
    functions=CONNECTIONS_FUNCTIONS,
    grants=CONNECTIONS_GRANTS,
    label="connections account",
    service="scheduler-connections",
    when_missing="tracker sync stops while cases keep working",
    process="tracker process",
    guide="docs/product/setup/trackers/github.md",
)


async def provision(dsn: str | None = None, *, say: Say = print) -> bool:
    """Create or update the account and apply its grants. True when the account is ready.

    Never raises for a refusal: an install with connections off needs no account, and cases
    work without a tracker (AC55), so `setup` says what is wrong and goes on."""
    password = os.environ.get(PASSWORD_VARIABLE, "")
    if not password:
        say(
            f"connections account: {PASSWORD_VARIABLE} is not set, so {CONNECTIONS_ACCOUNT} "
            "was not created; scheduler-connections cannot start, and tracker sync stops "
            "while cases keep working"
        )
        return False
    engine = create_async_engine(dsn or os.environ["POSTGRES_DSN"], pool_size=1, max_overflow=0)
    try:
        if not await role_accounts.prepare_account(engine, CONNECTIONS, password, say):
            return False
        await role_accounts.take_shared_rights(engine, CONNECTIONS, say)
        return True
    finally:
        await engine.dispose()
