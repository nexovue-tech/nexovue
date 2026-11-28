"""The tracker role's own Postgres account: its queue functions and its row-security policies

F22 (`docs/features/case-management/`), plan Step 8 (T8.7), by F3's pattern (ADR-040).

**Why this change is required.** `scheduler-connections` is the one role that opens tracker
credentials and parses answers from the internet (ADR-030), and until now it signed in as the
install's own account, so a compromise of it read and wrote all of Postgres and could lock the
alert queue. It now signs in as `nexovue_connections`, which `setup` creates and grants after
every migration (`nexovue_api.connections_account`), with column grants on its own tables
only. Like `nexovue_insight` it holds **no right on `jobs` or `job_results`**: a right to
change a table is a right to `LOCK` it, and those two tables carry every alert. These seven
functions take their place, each `SECURITY DEFINER`, with `search_path` fixed to
`pg_catalog, pg_temp` and every table named with its schema:

- `connections_claim_jobs`, `connections_retry_job`, `connections_finish_job`: `jobs/queue.py`'s
  `_CLAIM`, `_RETRY` and `_FINISH`, limited to the two tracker queues, as 0070 did for the AI
  role. `test_connections_account_live.py` holds them equal to the shared statements.
- `connections_enqueue_job`: `nexovue_platform.jobs.ENQUEUE_UNLESS_WAITING`, refusing any queue
  but the two tracker queues, so the account can add work only for itself.
- `connections_store_test_result`: `jobs/results.py`'s store with `kind = 'connection_test'`
  written in; the first result still stands.
- `connections_brief_request` and `connections_answer_brief`: F5's two statements on a
  `brief_paths` result (`source_pack.py`), with the kind written in.

**Row security.** `finding_fixes` and `finding_fix_source_packs` have row-level security with
policies for `PUBLIC` (0077, 0079), written for the AI account, which this account would meet
too. Two permissive policies let `nexovue_connections` reach every row, as the install's own
account does; what it may do there is still only its grants. The role is named, not granted
(`TO nexovue_connections`), because it exists only once `setup` runs.

**Affected store and schema.** Postgres: seven functions and two policies. No table or column.

**Compatibility and backfill.** None needed. `EXECUTE` is taken from `PUBLIC` here; `setup`
grants it to the account, and takes it from `PUBLIC` again after a restore.

**Rollback and recovery.** The downgrade drops what this adds; nothing here is a record. An
install rolled back past it must also give `scheduler-connections` the install's account
again, since its own account can claim nothing without these functions.

**Tenant, retention, and security impact.** The functions reach only the tracker queues and
the two result kinds, whoever calls them; every job and result row still carries its
`org_id`. No secret.

Revision ID: 0085
Revises: 0084
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of `nexovue_platform.connections`' two queue names and the account's name,
# held equal by `test_connections_account_live.py`: a later change is a migration of its own.
_QUEUES = "'connections', 'connections-interactive'"
_ACCOUNT = "nexovue_connections"

# No parameter shares a column's name: inside a SQL function a column name wins.
_FUNCTIONS = (
    f"""
    CREATE FUNCTION connections_claim_jobs(
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
              AND queue IN ({_QUEUES})
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
    CREATE FUNCTION connections_retry_job(
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
        WHERE id = job_id AND leased_by = worker AND queue IN ({_QUEUES})
    $$
    """,
    f"""
    CREATE FUNCTION connections_finish_job(job_id uuid, worker text)
    RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        DELETE FROM public.jobs WHERE id = job_id AND leased_by = worker AND queue IN ({_QUEUES})
    $$
    """,
    # A queue outside the two raises rather than adding nothing: a silent drop would hide the
    # bug that asked for it.
    f"""
    CREATE FUNCTION connections_enqueue_job(
        job_queue text, job_task text, job_kwargs jsonb, job_org uuid,
        countdown_seconds double precision, expires_seconds double precision,
        job_max_attempts integer, soft_seconds integer, hard_seconds integer
    )
    RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF job_queue IS NULL OR job_queue NOT IN ({_QUEUES}) THEN
            RAISE EXCEPTION 'connections_enqueue_job: queue % is not a tracker queue', job_queue
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        INSERT INTO public.jobs (
            queue, task, kwargs, org_id, run_after, expires_at,
            max_attempts, soft_limit_seconds, hard_limit_seconds
        )
        SELECT
            job_queue, job_task, job_kwargs, job_org,
            now() + make_interval(secs => countdown_seconds),
            now() + make_interval(secs => expires_seconds),
            job_max_attempts, soft_seconds, hard_seconds
        WHERE NOT EXISTS (
            SELECT 1 FROM public.jobs AS waiting
            WHERE waiting.org_id = job_org AND waiting.task = job_task
              AND waiting.kwargs = job_kwargs
              AND (waiting.lease_until IS NULL OR waiting.lease_until < now())
              AND (waiting.expires_at IS NULL OR waiting.expires_at > now())
              AND waiting.attempts < waiting.max_attempts
        );
    END
    $$
    """,
    """
    CREATE FUNCTION connections_store_test_result(
        for_request uuid, for_org uuid, result_payload jsonb, ttl_seconds double precision
    )
    RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        INSERT INTO public.job_results (request_id, org_id, kind, payload, expires_at)
        VALUES (
            for_request, for_org, 'connection_test', result_payload,
            now() + make_interval(secs => ttl_seconds)
        )
        ON CONFLICT (request_id) DO NOTHING
    $$
    """,
    """
    CREATE FUNCTION connections_brief_request(for_request uuid, for_org uuid)
    RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT payload FROM public.job_results
        WHERE org_id = for_org AND request_id = for_request AND kind = 'brief_paths'
    $$
    """,
    # The answer replaces the request's candidates once, so the first answer stands; it returns
    # who asked, for the audit row, or no row when there was nothing left to answer.
    """
    CREATE FUNCTION connections_answer_brief(
        for_request uuid, for_org uuid, answer jsonb
    )
    RETURNS TABLE (requested_by uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
        UPDATE public.job_results SET payload = answer
        WHERE org_id = for_org AND request_id = for_request AND kind = 'brief_paths'
          AND payload ? 'candidates'
        RETURNING CAST(payload ->> 'requested_by' AS uuid)
    $$
    """,
)
_SIGNATURES = (
    "connections_claim_jobs(text, text[], integer, integer)",
    "connections_retry_job(uuid, text, double precision, text)",
    "connections_finish_job(uuid, text)",
    "connections_enqueue_job(text, text, jsonb, uuid, double precision, double precision, "
    "integer, integer, integer)",
    "connections_store_test_result(uuid, uuid, jsonb, double precision)",
    "connections_brief_request(uuid, uuid)",
    "connections_answer_brief(uuid, uuid, jsonb)",
)

# `ALL`, since the grants already say which acts the account may do on these rows.
_POLICIES = (
    ("finding_fixes", "finding_fixes_connections_role"),
    ("finding_fix_source_packs", "finding_fix_source_packs_connections_role"),
)


def upgrade() -> None:
    for function in _FUNCTIONS:
        op.execute(function)
    for signature in _SIGNATURES:
        # A new function may be run by anyone until this; `setup` grants it to the account.
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    for table, policy in _POLICIES:
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR ALL "
            f"USING (current_user = '{_ACCOUNT}') WITH CHECK (current_user = '{_ACCOUNT}')"
        )


def downgrade() -> None:
    for table, policy in _POLICIES:
        op.execute(f"DROP POLICY {policy} ON {table}")
    for signature in _SIGNATURES:
        op.execute(f"DROP FUNCTION {signature}")
