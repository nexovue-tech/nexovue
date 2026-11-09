"""The GitHub connection's jobs, against real Postgres and the simulated GitHub
(F22 plan T3.6; AC38, AC74, and the connection states of AC79's scheduler half).

**Against a fake, by the founder's decision of 2026-11-08** (foundation item 28):
`tracker_fake.py` answers as GitHub's documentation reads, and its fixtures say none of
it was checked against GitHub. The founder's real run after Phase 2 (T3.0, T4.8) is what
checks the fake.

What is proved here:

- the manifest code converts once, is null in the same transaction, and never reaches a
  log; the client secret GitHub hands back is not kept; a code past its hour is not sent;
- binding reads the App's own installation, never one a request names (AC38);
- repositories are read with a token narrowed to metadata, revoked afterwards;
- a test moves the connection through its states, `failing` only after 5 straight;
- a job naming another organization's connection finds nothing and calls nothing.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, generate_key, keyring_from
from nexovue_platform.connections import (
    CHECK_CONNECTION_TASK,
    CONNECTION_CREDENTIAL_PURPOSE,
    CONNECTION_MANIFEST_CODE_PURPOSE,
    CONNECTION_TEST_RESULT_KIND,
    CONNECTION_WEBHOOK_SECRET_PURPOSE,
    FAILURES_BEFORE_FAILING,
    REFRESH_CONNECTION_TASK,
    connection_secret_owner,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs
from tracker_fake import CLIENT_SECRET, WEBHOOK_SECRET, FakeGitHub, serve_fake_github

from nexovue_scheduler.connections import tasks
from nexovue_scheduler.connections.github import GitHubEndpoint
from nexovue_scheduler.jobs.registry import registered

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_connections_github_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

NOW: Final = datetime.now(UTC).replace(microsecond=0)
KEYS: Final = ConfiguredKeys(generate_key())
OTHER_KEYS: Final = ConfiguredKeys(generate_key())


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with serve_fake_github() as fake:
        yield fake


def _endpoint(fake: FakeGitHub) -> GitHubEndpoint:
    # The fake listens on this machine's private address: the development escape.
    return GitHubEndpoint(base_url=fake.base_url, private_allowed=True)


def _box(purpose: str, keys: ConfiguredKeys = KEYS) -> SealedBox:
    return SealedBox(keyring_from(keys.current, keys.previous), purpose=purpose)


async def _connection(
    engine: AsyncEngine, tenant: AlertTenant, *, state: str = "setting_up", **columns: Any
) -> uuid.UUID:
    connection_id = uuid.uuid4()
    names = ", ".join(columns)
    values = ", ".join(f":{name}" for name in columns)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO connections (id, org_id, kind, state"
                + (f", {names}" if columns else "")
                + ") VALUES (:id, :org_id, 'github', :state"
                + (f", {values}" if columns else "")
                + ")"
            ),
            {"id": connection_id, "org_id": tenant.org_id, "state": state, **columns},
        )
    return connection_id


def _owner(tenant: AlertTenant, connection_id: uuid.UUID) -> str:
    return connection_secret_owner(str(tenant.org_id), str(connection_id))


async def _with_code(
    engine: AsyncEngine, tenant: AlertTenant, code: str, *, expires_in: timedelta
) -> uuid.UUID:
    connection_id = uuid.uuid4()
    sealed = _box(CONNECTION_MANIFEST_CODE_PURPOSE).seal(code, owner=_owner(tenant, connection_id))
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO connections (id, org_id, kind, state, sealed_manifest_code, "
                "setup_expires_at) VALUES (:id, :org_id, 'github', 'setting_up', :code, :expires)"
            ),
            {
                "id": connection_id,
                "org_id": tenant.org_id,
                "code": sealed,
                "expires": NOW + expires_in,
            },
        )
    return connection_id


async def _app_connection(
    engine: AsyncEngine,
    tenant: AlertTenant,
    fake: FakeGitHub,
    *,
    state: str = "setting_up",
    installation_id: int | None = None,
    keys: ConfiguredKeys = KEYS,
) -> uuid.UUID:
    """A connection whose App exists: its key sealed, as conversion leaves it."""
    connection_id = uuid.uuid4()
    sealed = _box(CONNECTION_CREDENTIAL_PURPOSE, keys).seal(
        fake.private_key_pem, owner=_owner(tenant, connection_id)
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO connections (id, org_id, kind, state, sealed_credential, "
                "github_app_id, github_installation_id) VALUES (:id, :org_id, 'github', "
                ":state, :key, :app_id, :installation_id)"
            ),
            {
                "id": connection_id,
                "org_id": tenant.org_id,
                "state": state,
                "key": sealed,
                "app_id": fake.app_id,
                "installation_id": installation_id,
            },
        )
    return connection_id


async def _row(engine: AsyncEngine, connection_id: uuid.UUID) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT * FROM connections WHERE id = :id"), {"id": connection_id}
            )
        ).one()


async def _places(engine: AsyncEngine, connection_id: uuid.UUID) -> dict[str, tuple[bool, bool]]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT key, is_private, is_reachable FROM connection_repositories "
                    "WHERE connection_id = :id"
                ),
                {"id": connection_id},
            )
        ).all()
    return {row.key: (row.is_private, row.is_reachable) for row in rows}


def _ids(tenant: AlertTenant, connection_id: uuid.UUID) -> dict[str, str]:
    return {"org_id": str(tenant.org_id), "connection_id": str(connection_id)}


# ------------------------------------------------------------- the manifest code


async def test_the_code_converts_once_and_is_gone_in_the_same_transaction(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    code = github.add_code()
    connection_id = await _with_code(engine, tenant, code, expires_in=timedelta(minutes=30))

    with capture_logs() as logged:
        await tasks.convert_manifest(
            engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
        )

    row = await _row(engine, connection_id)
    owner = _owner(tenant, connection_id)
    assert row.sealed_manifest_code is None
    assert row.github_app_id == github.app_id
    assert row.github_app_slug == "nexovue-acme"
    opened_key = _box(CONNECTION_CREDENTIAL_PURPOSE).open(bytes(row.sealed_credential), owner=owner)
    assert opened_key == github.private_key_pem
    webhook = _box(CONNECTION_WEBHOOK_SECRET_PURPOSE).open(
        bytes(row.sealed_webhook_secret), owner=owner
    )
    assert webhook == WEBHOOK_SECRET
    # AC74: the code, the key and the client secret reach no log line.
    printed = json.dumps(logged, default=str)
    for secret in (code, CLIENT_SECRET, WEBHOOK_SECRET, "PRIVATE KEY"):
        assert secret not in printed
    # Nothing in the row opens to the client secret: F22 keeps none (D41).
    assert CLIENT_SECRET.encode() not in json.dumps(row._asdict(), default=str).encode()


async def test_a_second_run_makes_no_call(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    code = github.add_code()
    connection_id = await _with_code(engine, tenant, code, expires_in=timedelta(minutes=30))
    ids = _ids(tenant, connection_id)
    await tasks.convert_manifest(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **ids)
    sent = len(github.calls("/app-manifests"))

    await tasks.convert_manifest(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **ids)

    assert len(github.calls("/app-manifests")) == sent == 1


async def test_a_code_past_its_hour_is_never_sent(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    """AC74: refused past one hour, and nulled."""
    code = github.add_code()
    connection_id = await _with_code(engine, tenant, code, expires_in=timedelta(minutes=-1))

    await tasks.convert_manifest(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    row = await _row(engine, connection_id)
    assert github.calls("/app-manifests") == []
    assert row.sealed_manifest_code is None
    assert row.last_failure == "manifest_expired"
    assert row.sealed_credential is None


async def test_a_code_github_refuses_is_dropped_with_the_reason(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    connection_id = await _with_code(
        engine, tenant, "a-code-github-never-issued", expires_in=timedelta(minutes=30)
    )

    await tasks.convert_manifest(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    row = await _row(engine, connection_id)
    assert row.sealed_manifest_code is None
    assert (row.last_failure, row.last_failure_status) == ("manifest_refused", 404)


async def test_github_down_keeps_the_code_for_the_job_s_next_attempt(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    code = github.add_code()
    github.fail("conversion", 502)
    connection_id = await _with_code(engine, tenant, code, expires_in=timedelta(minutes=30))

    with pytest.raises(tasks.Retry):
        await tasks.convert_manifest(
            engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
        )

    assert (await _row(engine, connection_id)).sealed_manifest_code is not None


# --------------------------------------------------------- binding and repositories


def test_no_job_accepts_an_installation_id() -> None:
    """AC38, the scheduler's half: what is bound is what GitHub lists for the App. A job
    row carrying an installation id fails at its call, before anything is read."""
    for name in (REFRESH_CONNECTION_TASK, CHECK_CONNECTION_TASK):
        parameters = inspect.signature(registered(("nexovue_scheduler.connections.tasks",))[name])
        assert not [p for p in parameters.parameters if "installation" in p]


async def test_refresh_binds_the_app_s_own_installation_and_reads_its_repositories(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    installation = github.install(3000042)
    github.add_repository(11, "acme/api", private=True)
    github.add_repository(12, "acme/site", private=False)
    github.add_repository(13, "acme/bad name", private=True)
    connection_id = await _app_connection(engine, tenant, github)

    await tasks.refresh(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    row = await _row(engine, connection_id)
    assert row.github_installation_id == installation
    assert row.state == "working"
    assert row.repositories_read_at is not None
    # A name outside the strict shape is never stored.
    assert await _places(engine, connection_id) == {
        "acme/api": (True, True),
        "acme/site": (False, True),
    }
    # The token was narrowed to reading repository metadata, and revoked after.
    [minted] = github.tokens.values()
    assert minted.permissions == {"metadata": "read"}
    assert minted.revoked
    # The App signed in with a short-lived token naming it.
    claims = github.jwt_claims_seen[0]
    assert claims["iss"] == str(github.app_id)
    assert claims["exp"] - claims["iat"] <= 600


async def test_a_repository_no_longer_reached_is_marked_unreachable(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    github.install()
    github.add_repository(11, "acme/api")
    github.add_repository(12, "acme/web")
    connection_id = await _app_connection(engine, tenant, github)
    ids = _ids(tenant, connection_id)
    await tasks.refresh(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **ids)
    github.repositories = github.repositories[:1]

    await tasks.refresh(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **ids)

    assert await _places(engine, connection_id) == {
        "acme/api": (True, True),
        "acme/web": (True, False),
    }


async def test_repositories_are_read_page_by_page(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    github.install()
    for number in range(105):
        github.add_repository(1000 + number, f"acme/service-{number}")
    connection_id = await _app_connection(engine, tenant, github)

    await tasks.refresh(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    assert len(await _places(engine, connection_id)) == 105


@pytest.mark.parametrize(
    ("installations", "failure"),
    [(0, "app_not_installed"), (2, "installation_ambiguous")],
)
async def test_an_app_installed_nowhere_or_twice_binds_nothing(
    engine: AsyncEngine,
    tenant: AlertTenant,
    github: FakeGitHub,
    installations: int,
    failure: str,
) -> None:
    for number in range(installations):
        github.install(3000100 + number)
    connection_id = await _app_connection(engine, tenant, github)

    await tasks.refresh(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    row = await _row(engine, connection_id)
    assert row.github_installation_id is None
    assert (row.state, row.last_failure) == ("setting_up", failure)
    assert await _places(engine, connection_id) == {}


async def test_an_installation_reaching_no_repository_says_so(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    github.install()
    connection_id = await _app_connection(engine, tenant, github)

    await tasks.refresh(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    row = await _row(engine, connection_id)
    assert (row.state, row.last_failure) == ("setting_up", "no_repository")


# ------------------------------------------------------------------- the test


async def test_a_passing_test_makes_the_connection_working_and_leaves_its_result(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    installation = github.install()
    github.add_repository(11, "acme/api")
    connection_id = await _app_connection(engine, tenant, github, installation_id=installation)
    request_id = str(uuid.uuid4())

    await tasks.check(
        engine,
        keys=KEYS,
        endpoint=_endpoint(github),
        now=NOW,
        request_id=request_id,
        **_ids(tenant, connection_id),
    )

    row = await _row(engine, connection_id)
    assert row.state == "working"
    assert row.last_ok_at is not None
    assert all(minted.revoked for minted in github.tokens.values())
    async with engine.connect() as connection:
        result = (
            await connection.execute(
                text(
                    "SELECT payload FROM job_results WHERE request_id = :id AND org_id = :org "
                    "AND kind = :kind"
                ),
                {"id": request_id, "org": tenant.org_id, "kind": CONNECTION_TEST_RESULT_KIND},
            )
        ).scalar_one()
    assert result == {"ok": True, "failure": None, "state": "working"}


async def test_a_working_connection_fails_only_after_five_straight_failures(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    """AC79: `failing` after 5 straight, and one success brings it back."""
    installation = github.install()
    github.add_repository(11, "acme/api")
    connection_id = await _app_connection(
        engine, tenant, github, state="working", installation_id=installation
    )
    ids = _ids(tenant, connection_id)
    github.fail("app", 503)

    for attempt in range(1, FAILURES_BEFORE_FAILING + 1):
        await tasks.check(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **ids)
        row = await _row(engine, connection_id)
        expected = "failing" if attempt == FAILURES_BEFORE_FAILING else "working"
        assert (row.state, row.consecutive_failures) == (expected, attempt)
        assert (row.last_failure, row.last_failure_status) == ("server_error", 503)
    assert row.failing_since is not None

    github.faults.clear()
    await tasks.check(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **ids)

    row = await _row(engine, connection_id)
    assert (row.state, row.consecutive_failures, row.failing_since) == ("working", 0, None)
    assert row.last_failure is None


async def test_a_refused_sign_in_needs_a_person(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    installation = github.install()
    connection_id = await _app_connection(
        engine, tenant, github, state="working", installation_id=installation
    )
    github.fail("app", 401)

    await tasks.check(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    row = await _row(engine, connection_id)
    assert (row.state, row.last_failure, row.last_failure_status) == (
        "needs_attention",
        "unauthorized",
        401,
    )


async def test_a_key_this_install_cannot_open_calls_nothing(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    """Design, *Failure handling*: the operator's key is wrong; no call is made."""
    installation = github.install()
    connection_id = await _app_connection(
        engine, tenant, github, state="working", installation_id=installation, keys=OTHER_KEYS
    )

    await tasks.check(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    row = await _row(engine, connection_id)
    assert (row.state, row.last_failure) == ("needs_attention", "key_unreadable")
    assert github.site.requests == []


async def test_a_job_naming_another_organization_s_connection_finds_nothing(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    """AC129: every read binds the job's own `org_id`; a mismatch stops before any call."""
    installation = github.install()
    connection_id = await _app_connection(
        engine, tenant, github, state="working", installation_id=installation
    )
    stranger = {"org_id": str(uuid.uuid4()), "connection_id": str(connection_id)}

    await tasks.check(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **stranger)
    await tasks.refresh(engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **stranger)

    assert github.site.requests == []
    assert (await _row(engine, connection_id)).last_ok_at is None


async def test_a_disconnected_connection_is_left_alone(
    engine: AsyncEngine, tenant: AlertTenant, github: FakeGitHub
) -> None:
    connection_id = await _connection(engine, tenant, state="disconnected")

    await tasks.check(
        engine, keys=KEYS, endpoint=_endpoint(github), now=NOW, **_ids(tenant, connection_id)
    )

    assert github.site.requests == []
    assert (await _row(engine, connection_id)).state == "disconnected"
