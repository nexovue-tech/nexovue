"""Suggestions that read a repository's files: consent, code locations, the GitHub link

F5 (`docs/features/ai-solution/`), plan Step 3 (T3.1, T3.3, T3.16, T3.17); ADR-082 to ADR-084.

**Why this change is required.** Rung 3 reads a service's files from its repository, lists
them for a person, and stops (Step 3); Step 4 sends them. F5's second migration of three
(design, *Data and migration impact*) adds what that needs:

- on `finding_fixes`: the source columns, without the pull request columns (Step 7), the
  `source` kind, and the CHECK that a general row holds none of them (AC75, open decision
  27); the states `gathering`, `gathered` and `stale`; Step 3's reasons; the one-in-flight
  index widened to every working state (BR8);
- `finding_fix_source_packs` (the files read, minutes long), `finding_fix_checks` (Step 4's
  check runs), `finding_fix_code_views` (a person's first read of quoted code);
- `service_code_locations`: a service's repository and folder, for the organization with a
  project's override (FR5b, ADR-083);
- `fix_source_consents`: an owner's agreement per repository and folder, bound to the
  provider (FR6, FR8, ADR-084);
- `github_user_links`, `github_link_attempts`: a person's GitHub identity, no token, for the
  owner's access requirement (D20, A18);
- on F22's `connections`: the Fix App's client id, its sealed client secret, and the
  permissions GitHub granted (ADR-082); its "disconnected holds no secret" check grows;
- `brief_paths` in `job_results.kind` (FR5c);
- `fix_request_more_files`, the one function that moves a row back to `gathering` when the
  model asks for files (D75). Its `EXECUTE` for the AI account is T4.13's, in `setup`.

**Row-level security only; grants are `setup`'s** (design, *Grants*). The pack's policies
admit the AI account to a pack only while its suggestion is `proposing`; they name no
role, since `nexovue_insight` exists only once `setup` runs.

**Affected store and schema.** Postgres: seven new tables, columns on two, three checks
replaced (`NOT VALID`, then validated), one index replaced concurrently, one function.

**Compatibility and backfill.** None needed: every new column is nullable or defaulted,
and every check only widens, except the kind check, which every existing row (`general`,
holding none of the source columns) already meets.

**Rollback and recovery.** Roll forward only: consents and code locations are an owner's
and an admin's recorded acts, and a suggestion's files read are the record of what was
read from a customer's repository.

**Tenant, retention, and security impact.** Every table carries `org_id`, bound by
composite keys to its finding, suggestion, repository or person, so the database refuses a
row that crosses organizations (AC30); each goes with its parent (`organizations/tables.py`).
Two sealed columns join `keys.SEALED_COLUMNS`: the client secret and a link attempt's code,
under `CONNECTION_SECRET_KEY`. The pack holds whole files for minutes and is deleted when
its run ends; `clear_stored_code` (`nexovue_platform/fixes/statements.py`) empties every
source column and table listed here. A GitHub link is personal data: exported, and deleted
when its person is.

Revision ID: 0079
Revises: 0078
"""

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TIMESTAMP = sa.DateTime(timezone=True)
_JSONB = postgresql.JSONB()

# Frozen copies of `nexovue_platform.fixes.records`' lists, held equal by
# `test_fix_source_storage.py`: a later change to them is a migration of its own.
_FIX_KINDS = "'source', 'general'"
_FIX_STATES = "'gathering', 'gathered', 'proposing', 'proposed', 'refused', 'failed', 'stale'"
_IN_FLIGHT = "'gathering', 'gathered', 'proposing'"
_WITH_A_REASON = "'refused', 'failed', 'stale'"
_FIX_REASONS = (
    "'evidence_too_thin', 'needs_a_decision', 'not_a_code_problem', 'general_withheld_G3', "
    "'general_withheld_G4', 'general_withheld_G5', 'general_withheld_G6', "
    "'general_withheld_G7', 'general_withheld_G8', 'general_withheld_G9', 'ai_off', "
    "'project_opted_out', 'consent_ended_in_flight', 'limit_reached', 'provider_refused', "
    "'provider_too_long', 'provider_unavailable', 'invalid_output', 'timed_out', "
    "'nexovue_error', 'no_usable_file', 'stack_aged_out', 'no_stack', "
    "'repository_too_large', 'cancelled', 'lapsed', 'github_unavailable', "
    "'permission_missing', 'not_linked', 'no_github_access', 'access_unknown', "
    "'rules_changed', 'mapping_changed', 'cause_changed', 'edit_no_longer_applies'"
)
_BASES = "'likely_cause', 'stack_only', 'named_files'"
_CANDIDATE_SOURCES = "'stack', 'person', 'model'"
_CODE_DELETED_REASONS = (
    "'ended_short', 'pull_request_done', 'aged_out', 'consent_withdrawn', 'disconnected', "
    "'ai_off', 'access_gone', 'restored'"
)
_CHECK_PURPOSES = "'patch', 'draft', 'read'"
_CHECK_STATUSES = "'running', 'unchanged', 'moved', 'stale', 'refused', 'failed'"
_LINK_STATUSES = "'waiting', 'running', 'linked', 'refused', 'failed'"
_LINK_REASONS = "'expired', 'refused_by_github', 'github_unavailable', 'key_unreadable'"
_CONSENT_WITHDRAWN_REASONS = "'owner', 'disconnected', 'access_gone', 'restored'"
# The design's caps: edits 64 KiB, the hint 500 characters, 3 rounds (D74).
_EDITS_MAX_BYTES = 64 * 1024
_HINT_MAX_CHARACTERS = 500
_MAX_ROUNDS = 3
# A folder (FR5b): relative, no `..` segment, no control character. A path in production
# may start with `/`.
_FOLDER = r"^[^/\x01-\x1f\x7f]([^\x01-\x1f\x7f]*[^/\x01-\x1f\x7f])?$"
_NO_DOT_DOT = r"(^|/)\.\.(/|$)"
_PRODUCTION_PATH = r"^[^\x01-\x1f\x7f]+$"
# A GitHub login (its documented shape, general knowledge): 1 to 39 letters, digits, hyphens.
_GITHUB_LOGIN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"

_JOB_RESULT_KINDS = "'check_run', 'channel_test', 'connection_test', 'ai_check', 'brief_paths'"
_CONNECTION_SECRETS_CLEARED = (
    "state <> 'disconnected' OR (sealed_credential IS NULL "
    "AND sealed_webhook_secret IS NULL AND sealed_manifest_code IS NULL "
    "AND sealed_client_secret IS NULL AND setup_state_hash IS NULL)"
)

_PACK_POLICIES = (
    "ALTER TABLE finding_fix_source_packs ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY finding_fix_source_packs_ai_reads ON finding_fix_source_packs FOR SELECT
    USING (EXISTS (SELECT 1 FROM finding_fixes f
                   WHERE f.id = fix_id AND f.org_id = finding_fix_source_packs.org_id
                     AND f.state = 'proposing'))
    """,
    """
    CREATE POLICY finding_fix_source_packs_ai_deletes ON finding_fix_source_packs FOR DELETE
    USING (EXISTS (SELECT 1 FROM finding_fixes f
                   WHERE f.id = fix_id AND f.org_id = finding_fix_source_packs.org_id
                     AND f.state = 'proposing'))
    """,
)

# D75: the AI account's only way back to `gathering`. Its task and queue are fixed here,
# and its job carries ids only.
_REQUEST_MORE_FILES_SIGNATURE = "fix_request_more_files(uuid, uuid)"
_REQUEST_MORE_FILES = f"""
CREATE FUNCTION fix_request_more_files(for_fix uuid, for_org uuid)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    UPDATE public.finding_fixes SET
        state = 'gathering',
        state_changed_at = now(),
        candidates_from = 'model',
        round = round + 1
    WHERE id = for_fix AND org_id = for_org AND kind = 'source'
      AND state = 'proposing' AND requested_paths IS NOT NULL AND round < {_MAX_ROUNDS};
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    UPDATE public.finding_fix_source_packs SET expires_at = now() + interval '15 minutes'
    WHERE fix_id = for_fix AND org_id = for_org;
    INSERT INTO public.jobs (
        queue, task, kwargs, org_id, run_after, expires_at,
        max_attempts, soft_limit_seconds, hard_limit_seconds
    ) VALUES (
        'connections-interactive', 'nexovue_scheduler.connections.build_source_pack',
        jsonb_build_object('fix_id', for_fix::text, 'org_id', for_org::text), for_org,
        now(), now() + interval '15 minutes', 3, 60, 90
    );
    RETURN true;
END
$$
"""


def _org() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "org_id", _UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _fix_key(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id", "fix_id"],
        ["finding_fixes.org_id", "finding_fixes.id"],
        ondelete="CASCADE",
        name=f"fk_{table}_fix",
    )


def _repository_key(table: str, *, ondelete: str = "CASCADE") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id", "connection_repository_id"],
        ["connection_repositories.org_id", "connection_repositories.id"],
        ondelete=ondelete,
        name=f"fk_{table}_repository",
    )


def _person_key(table: str, column: str, *, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id", column],
        ["users.org_id", "users.id"],
        ondelete=ondelete,
        name=f"fk_{table}_{column}",
    )


def _replace_check(table: str, name: str, condition: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def _add_check(table: str, name: str, condition: str) -> None:
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def _upgrade_finding_fixes() -> None:
    columns: tuple[sa.Column[Any], ...] = (
        sa.Column("connection_repository_id", _UUID, nullable=True),
        sa.Column("folder", sa.Text(), nullable=True),
        sa.Column("basis", sa.Text(), nullable=True),
        # Attacker-writable stack paths, cleared on every way out (FR11).
        sa.Column("path_candidates", _JSONB, nullable=True),
        # The model's asked-for names, untrusted, cleared by the pack job (FR15b).
        sa.Column("requested_paths", _JSONB, nullable=True),
        sa.Column("round", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("candidates_from", sa.Text(), nullable=True),
        sa.Column("read_commit", sa.Text(), nullable=True),
        sa.Column("read_commit_known", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Written only by `scheduler-connections` (design, `files_read`).
        sa.Column("files_read", _JSONB, nullable=True),
        sa.Column("checks_version", sa.Text(), nullable=True),
        sa.Column("extra_list_hash", sa.Text(), nullable=True),
        sa.Column("files_digest", sa.Text(), nullable=True),
        # Source, written by the AI room from Step 4 (D53's display facts inside).
        sa.Column("edits", _JSONB, nullable=True),
        sa.Column("withheld_refs", _JSONB, nullable=True),
        sa.Column("code_deleted_at", _TIMESTAMP, nullable=True),
        sa.Column("code_deleted_reason", sa.Text(), nullable=True),
        sa.Column("notify_requester", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("email_due", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_by", _UUID, nullable=True),
        sa.Column("sent_at", _TIMESTAMP, nullable=True),
    )
    for column in columns:
        op.add_column("finding_fixes", column)
    # A source row may start from typed paths with no explanation (design: `int null`).
    op.alter_column("finding_fixes", "explanation_version", nullable=True)
    op.create_foreign_key(
        "fk_finding_fixes_repository",
        "finding_fixes",
        "connection_repositories",
        ["org_id", "connection_repository_id"],
        ["org_id", "id"],
        ondelete="SET NULL (connection_repository_id)",
    )
    op.create_foreign_key(
        "fk_finding_fixes_sent_by",
        "finding_fixes",
        "users",
        ["org_id", "sent_by"],
        ["org_id", "id"],
        ondelete="SET NULL (sent_by)",
    )
    table = "finding_fixes"
    _replace_check(table, "ck_finding_fixes_kind_is_known", f"kind IN ({_FIX_KINDS})")
    _replace_check(table, "ck_finding_fixes_state_is_known", f"state IN ({_FIX_STATES})")
    _replace_check(
        table, "ck_finding_fixes_reason_is_known", f"reason IS NULL OR reason IN ({_FIX_REASONS})"
    )
    _replace_check(
        table,
        "ck_finding_fixes_reason_only_when_it_ended_short",
        f"(state IN ({_WITH_A_REASON})) = (reason IS NOT NULL)",
    )
    checks = {
        # AC75's storage half: a general row holds no repository, folder, files or edits.
        "ck_finding_fixes_general_holds_no_source": (
            "kind = 'source' OR (connection_repository_id IS NULL AND folder IS NULL "
            "AND files_read IS NULL AND edits IS NULL AND path_candidates IS NULL "
            "AND requested_paths IS NULL)"
        ),
        "ck_finding_fixes_general_names_its_version": (
            "kind <> 'general' OR explanation_version IS NOT NULL"
        ),
        "ck_finding_fixes_basis_is_known": f"basis IS NULL OR basis IN ({_BASES})",
        "ck_finding_fixes_candidates_from_is_known": (
            f"candidates_from IS NULL OR candidates_from IN ({_CANDIDATE_SOURCES})"
        ),
        "ck_finding_fixes_round_in_range": f"round BETWEEN 1 AND {_MAX_ROUNDS}",
        "ck_finding_fixes_edits_are_small": (
            f"edits IS NULL OR octet_length(edits::text) <= {_EDITS_MAX_BYTES}"
        ),
        "ck_finding_fixes_code_deleted_reason_is_known": (
            "(code_deleted_at IS NULL) = (code_deleted_reason IS NULL) AND "
            f"(code_deleted_reason IS NULL OR code_deleted_reason IN ({_CODE_DELETED_REASONS}))"
        ),
        # Candidates live only while files are being chosen (FR11, AC12g).
        "ck_finding_fixes_candidates_only_while_gathering": (
            "path_candidates IS NULL OR state = 'gathering'"
        ),
    }
    for name, condition in checks.items():
        _add_check(table, name, condition)


def _create_pack_and_checks() -> None:
    op.create_table(
        "finding_fix_source_packs",
        sa.Column("fix_id", _UUID, primary_key=True),
        _org(),
        # `[{ref, path, content, window}]`: whole files for K3, the window sent.
        sa.Column("files", _JSONB, nullable=False),
        # `[{ref, line}]`, numbers only (FR15a).
        sa.Column("frames", _JSONB, nullable=False),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", _TIMESTAMP, nullable=False),
        _fix_key("finding_fix_source_packs"),
        sa.CheckConstraint(
            f"hint IS NULL OR length(hint) <= {_HINT_MAX_CHARACTERS}", name="hint_is_short"
        ),
        sa.CheckConstraint("bytes >= 0", name="bytes_count_up"),
    )
    op.create_index(
        "ix_finding_fix_source_packs_expires_at", "finding_fix_source_packs", ["expires_at"]
    )
    op.create_table(
        "finding_fix_checks",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("fix_id", _UUID, nullable=False),
        sa.Column("requested_by", _UUID, nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("head_sha", sa.Text(), nullable=True),
        sa.Column("blob_ids", _JSONB, nullable=True),
        sa.Column("moved_refs", _JSONB, nullable=True),
        # Source: on the deletion list.
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("digest", sa.Text(), nullable=True),
        sa.Column("requested_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", _TIMESTAMP, nullable=True),
        sa.Column("expires_at", _TIMESTAMP, nullable=False),
        _fix_key("finding_fix_checks"),
        _person_key("finding_fix_checks", "requested_by", ondelete="CASCADE"),
        sa.UniqueConstraint(
            "fix_id", "requested_by", "purpose", name="uq_finding_fix_checks_one_per_person"
        ),
        sa.CheckConstraint(f"purpose IN ({_CHECK_PURPOSES})", name="purpose_is_known"),
        sa.CheckConstraint(f"status IN ({_CHECK_STATUSES})", name="status_is_known"),
    )
    op.create_index("ix_finding_fix_checks_expires_at", "finding_fix_checks", ["expires_at"])
    op.create_table(
        "finding_fix_code_views",
        sa.Column("fix_id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, primary_key=True),
        _org(),
        sa.Column("first_viewed_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        _fix_key("finding_fix_code_views"),
        _person_key("finding_fix_code_views", "user_id", ondelete="CASCADE"),
    )


def _create_locations_and_consents() -> None:
    op.create_table(
        "service_code_locations",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        # Null for the whole organization; a project's row overrides it (FR5b).
        sa.Column("project_id", _UUID, nullable=True),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("connection_repository_id", _UUID, nullable=False),
        # Null for the whole repository.
        sa.Column("folder", sa.Text(), nullable=True),
        sa.Column("path_in_production", sa.Text(), nullable=True),
        sa.Column("set_by", _UUID, nullable=True),
        sa.Column("set_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_service_code_locations_project",
        ),
        _repository_key("service_code_locations"),
        _person_key("service_code_locations", "set_by", ondelete="SET NULL (set_by)"),
        sa.CheckConstraint("length(service_name) BETWEEN 1 AND 255", name="service_is_named"),
        sa.CheckConstraint(
            f"folder IS NULL OR (folder ~ '{_FOLDER}' AND folder !~ '{_NO_DOT_DOT}' "
            "AND length(folder) <= 200)",
            name="folder_is_relative",
        ),
        sa.CheckConstraint(
            f"path_in_production IS NULL OR (path_in_production ~ '{_PRODUCTION_PATH}' "
            f"AND path_in_production !~ '{_NO_DOT_DOT}' AND length(path_in_production) <= 200)",
            name="path_in_production_is_plain",
        ),
    )
    op.create_index(
        "uq_service_code_locations_service",
        "service_code_locations",
        ["org_id", "project_id", "service_name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_service_code_locations_repository",
        "service_code_locations",
        ["org_id", "connection_repository_id"],
    )
    op.create_table(
        "fix_source_consents",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("connection_repository_id", _UUID, nullable=False),
        sa.Column("provider_base_url", sa.Text(), nullable=False),
        sa.Column("provider_format", sa.Text(), nullable=False),
        sa.Column("key_id", _UUID, nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        # Null for the whole repository.
        sa.Column("folder_scope", sa.Text(), nullable=True),
        sa.Column("extra_excluded_paths", _JSONB, nullable=False, server_default="[]"),
        sa.Column("allowed_config_paths", _JSONB, nullable=False, server_default="[]"),
        # Set only from the owner's answer, with nothing preselected (D20).
        sa.Column("require_github_access", sa.Boolean(), nullable=False),
        sa.Column("ended_notice_sent_at", _TIMESTAMP, nullable=True),
        sa.Column("consented_by", _UUID, nullable=True),
        sa.Column("consented_at", _TIMESTAMP, nullable=False),
        sa.Column("withdrawn_at", _TIMESTAMP, nullable=True),
        sa.Column("withdrawn_by", _UUID, nullable=True),
        sa.Column("withdrawn_reason", sa.Text(), nullable=True),
        _repository_key("fix_source_consents"),
        _person_key("fix_source_consents", "consented_by", ondelete="SET NULL (consented_by)"),
        _person_key("fix_source_consents", "withdrawn_by", ondelete="SET NULL (withdrawn_by)"),
        sa.CheckConstraint(
            f"folder_scope IS NULL OR (folder_scope ~ '{_FOLDER}' "
            f"AND folder_scope !~ '{_NO_DOT_DOT}' AND length(folder_scope) <= 200)",
            name="folder_scope_is_relative",
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_reason IS NULL) AND (withdrawn_reason IS NULL "
            f"OR withdrawn_reason IN ({_CONSENT_WITHDRAWN_REASONS}))",
            name="withdrawn_reason_is_known",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(extra_excluded_paths) = 'array' "
            "AND jsonb_typeof(allowed_config_paths) = 'array'",
            name="path_lists_are_lists",
        ),
    )
    # One live consent per repository and folder; two folders may each have one (FR6).
    op.create_index(
        "uq_fix_source_consents_live",
        "fix_source_consents",
        ["connection_repository_id", "folder_scope"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
    )
    op.create_index(
        "ix_fix_source_consents_org_id_repository",
        "fix_source_consents",
        ["org_id", "connection_repository_id"],
    )


def _create_github_links() -> None:
    op.create_table(
        "github_user_links",
        sa.Column("user_id", _UUID, primary_key=True),
        _org(),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.Text(), nullable=False),
        sa.Column("linked_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        _person_key("github_user_links", "user_id", ondelete="CASCADE"),
        sa.CheckConstraint("github_user_id > 0", name="github_user_id_is_positive"),
        sa.CheckConstraint(f"github_login ~ '{_GITHUB_LOGIN}'", name="github_login_is_strict"),
    )
    op.create_table(
        "github_link_attempts",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        _org(),
        sa.Column("user_id", _UUID, nullable=False),
        # SHA-256 hex of the session that started it, and of its one-time `state` (A18).
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        # GitHub's one-time code, under `CONNECTION_SECRET_KEY`, never in a job row (AC15).
        sa.Column("sealed_code", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="waiting"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", _TIMESTAMP, nullable=False),
        _person_key("github_link_attempts", "user_id", ondelete="CASCADE"),
        sa.UniqueConstraint("state_hash", name="uq_github_link_attempts_state_hash"),
        sa.CheckConstraint(f"status IN ({_LINK_STATUSES})", name="status_is_known"),
        sa.CheckConstraint(
            f"reason IS NULL OR reason IN ({_LINK_REASONS})", name="reason_is_known"
        ),
        # The code lives only between the redirect back and its exchange.
        sa.CheckConstraint(
            "sealed_code IS NULL OR status = 'running'", name="code_only_while_running"
        ),
    )
    op.create_index("ix_github_link_attempts_expires_at", "github_link_attempts", ["expires_at"])


def _upgrade_connections() -> None:
    op.add_column("connections", sa.Column("github_client_id", sa.Text(), nullable=True))
    op.add_column("connections", sa.Column("sealed_client_secret", sa.LargeBinary(), nullable=True))
    op.add_column("connections", sa.Column("granted_permissions", _JSONB, nullable=True))
    op.add_column("connections", sa.Column("granted_at", _TIMESTAMP, nullable=True))
    _replace_check(
        "connections", "ck_connections_disconnected_holds_no_secret", _CONNECTION_SECRETS_CLEARED
    )
    _replace_check("job_results", "ck_job_results_kind_is_known", f"kind IN ({_JOB_RESULT_KINDS})")


def _widen_one_in_flight() -> None:
    """BR8 over every working state: the new index built concurrently, then the old one
    dropped, so writes to `finding_fixes` continue (`test_migration_rules.py`)."""
    with op.get_context().autocommit_block():
        # An earlier failed build leaves an invalid index of this name; build it again.
        op.drop_index(
            "uq_finding_fixes_one_working",
            "finding_fixes",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            "uq_finding_fixes_one_working",
            "finding_fixes",
            ["finding_id"],
            unique=True,
            postgresql_where=sa.text(f"state IN ({_IN_FLIGHT})"),
            postgresql_concurrently=True,
        )
        op.drop_index(
            "uq_finding_fixes_one_in_flight",
            "finding_fixes",
            postgresql_concurrently=True,
            if_exists=True,
        )


def upgrade() -> None:
    _upgrade_finding_fixes()
    _create_pack_and_checks()
    _create_locations_and_consents()
    _create_github_links()
    _upgrade_connections()
    for statement in (*_PACK_POLICIES, _REQUEST_MORE_FILES):
        op.execute(statement)
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_REQUEST_MORE_FILES_SIGNATURE} FROM PUBLIC")
    _widen_one_in_flight()


def downgrade() -> None:
    raise RuntimeError(
        "roll forward only: consents, code locations and the record of what was read from a "
        "customer's repository are found nowhere else. Going back past 0079 is a restore."
    )
