"""Slugs, keys, and the shape of what signup returns."""

from __future__ import annotations

import pytest

from nexovue_api.auth.clients import ClientDescription
from nexovue_api.data.postgres.models import API_KEY_PREFIX_LENGTH, SLUG_MAX_LENGTH
from nexovue_api.signup.apikeys import (
    API_KEY_PREFIX,
    API_KEY_SECRET_LENGTH,
    generate_api_key,
    hash_api_key,
)
from nexovue_api.signup.slugs import FALLBACK_SLUG, disambiguate, slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Acme Inc", "acme-inc"),
        ("  Acme   Inc  ", "acme-inc"),
        ("Acme, Inc.", "acme-inc"),
        ("ACME", "acme"),
        ("Ünïcôde Ltd", "unicode-ltd"),
        ("123 Industries", "123-industries"),
    ],
)
def test_a_name_becomes_a_url_safe_slug(name: str, expected: str) -> None:
    assert slugify(name) == expected


@pytest.mark.parametrize("name", ["###", "   ", "-", "!!!"])
def test_a_name_with_nothing_slugable_in_it_still_yields_a_slug(name: str) -> None:
    assert slugify(name) == FALLBACK_SLUG


def test_a_slug_never_exceeds_the_column() -> None:
    slug = slugify("A" * 200 + " Corporation")

    assert len(slug) <= SLUG_MAX_LENGTH
    assert not slug.endswith("-")


def test_the_first_organization_of_a_name_keeps_the_plain_slug() -> None:
    assert disambiguate("acme-inc", 0) == "acme-inc"


def test_a_later_organization_of_the_same_name_is_suffixed() -> None:
    assert disambiguate("acme-inc", 1) == "acme-inc-2"
    assert disambiguate("acme-inc", 2) == "acme-inc-3"


def test_a_suffix_never_pushes_a_slug_past_the_column() -> None:
    base = "a" * SLUG_MAX_LENGTH

    for attempt in range(1, 12):
        assert len(disambiguate(base, attempt)) <= SLUG_MAX_LENGTH


def test_a_key_has_the_documented_shape() -> None:
    key = generate_api_key()

    assert key.startswith(API_KEY_PREFIX)
    assert len(key) == len(API_KEY_PREFIX) + API_KEY_SECRET_LENGTH
    assert key[len(API_KEY_PREFIX) :].isalnum()


def test_the_stored_prefix_is_long_enough_to_look_a_key_up() -> None:
    assert len(API_KEY_PREFIX) < API_KEY_PREFIX_LENGTH


def test_two_keys_are_never_the_same() -> None:
    assert len({generate_api_key() for _ in range(200)}) == 200


def test_a_key_hash_is_stable_and_is_not_the_key() -> None:
    key = generate_api_key()

    assert hash_api_key(key) == hash_api_key(key)
    assert key not in hash_api_key(key)


def test_the_seed_key_matches_the_format_signup_produces() -> None:
    from nexovue_api.seed import DEV_API_KEY

    assert DEV_API_KEY.startswith(API_KEY_PREFIX)
    assert len(DEV_API_KEY) == len(API_KEY_PREFIX) + API_KEY_SECRET_LENGTH


def test_the_seed_site_key_matches_the_format_the_browser_door_accepts() -> None:
    """A malformed dev key fails at the receiver, where the message is about a
    key rather than about the seed that wrote it."""
    from nexovue_api.apikeys.sitekeys import SITE_KEY_RANDOM_LENGTH, SITE_KEY_SCHEME
    from nexovue_api.seed import DEV_SITE_KEY

    assert DEV_SITE_KEY.startswith(SITE_KEY_SCHEME)
    assert len(DEV_SITE_KEY) == len(SITE_KEY_SCHEME) + SITE_KEY_RANDOM_LENGTH


def test_the_seed_site_key_allows_every_origin_the_dev_stack_serves() -> None:
    """An origin missing from the list refuses 100% of that origin's traffic,
    and the failure is indistinguishable from the SDK not working."""
    from nexovue_api.seed import DEV_SITE_KEY_ORIGINS

    assert DEV_SITE_KEY_ORIGINS, "a key with no origins accepts nothing"
    assert all(origin.startswith("http://") for origin in DEV_SITE_KEY_ORIGINS)
    # Both spellings of the dev host: a browser sends whichever the address bar
    # holds, and they are different origins to the receiver.
    assert any("localhost" in origin for origin in DEV_SITE_KEY_ORIGINS)
    assert any("127.0.0.1" in origin for origin in DEV_SITE_KEY_ORIGINS)


class _OrderLog:
    """What the signup route did, in the order it did it."""

    def __init__(self) -> None:
        self.events: list[str] = []


class _RecordingDbSession:
    """A transaction that records its commit rather than performing one."""

    def __init__(self, log: _OrderLog) -> None:
        self._log = log

    def add(self, entity: object) -> None:
        """Audit rows the route writes."""

    async def commit(self) -> None:
        self._log.events.append("commit")


class _RecordingSessionStore:
    def __init__(self, log: _OrderLog) -> None:
        self._log = log

    async def create(
        self, *, user_id: str, org_id: str, client: ClientDescription | None = None
    ) -> tuple[str, object]:
        from datetime import UTC, datetime

        from nexovue_api.auth.sessions import Session

        self._log.events.append("session")
        return "token", Session(user_id=user_id, org_id=org_id, created_at=datetime.now(UTC))


async def _sign_up(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, object]
) -> tuple[object, _OrderLog, dict[str, object]]:
    """POST /signup with provisioning replaced, returning the response, what the route
    did in order, and what it asked provisioning for."""
    import uuid
    from collections.abc import AsyncIterator
    from typing import Any

    import httpx
    from environment import TEST_ENVIRONMENT
    from fake_stores import use_fake_second_factors
    from pydantic import SecretStr

    from nexovue_api.data.postgres.models import Organization, Project, User
    from nexovue_api.deps import (
        get_app_settings,
        get_db_session,
        get_one_time_tokens,
        get_optional_email_sender,
        get_session_store,
    )
    from nexovue_api.main import create_app
    from nexovue_api.settings import API_PREFIX, Settings
    from nexovue_api.signup import service

    log = _OrderLog()
    asked: dict[str, object] = {}
    # Signup is closed unless a deployment opens it, so a suite that drives the
    # public endpoint says so — the same sentence `docker-compose.yaml` says for
    # development (`docs/changes/signup-closed-by-default/`).
    settings = Settings(**{**TEST_ENVIRONMENT, "SIGNUP_ENABLED": "true"})  # type: ignore[arg-type]

    organization = Organization(slug="acme", name="Acme Inc")
    organization.id = uuid.uuid4()
    user = User(
        org_id=organization.id,
        email="owner@acme.test",
        name="Owner",
        password_hash="x",
        role="owner",
    )
    user.id = uuid.uuid4()
    project = Project(org_id=organization.id, slug="default", name="Default")
    project.id = uuid.uuid4()

    async def provision(_db: Any, **kwargs: Any) -> service.ProvisionedTenant:
        log.events.append("provision")
        asked.update(kwargs)
        return service.ProvisionedTenant(
            organization=organization,
            user=user,
            project=project,
            api_key=SecretStr("nxv_key"),
        )

    async def db_override() -> AsyncIterator[Any]:
        yield _RecordingDbSession(log)

    app = create_app(settings)
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_session_store] = lambda: _RecordingSessionStore(log)
    # Sign-in asks whether a second factor is required, so every suite that
    # drives it needs that store too.
    use_fake_second_factors(app)
    # No mail provider: signup logs and carries on, which is its documented
    # behaviour and keeps these tests about the route.
    app.dependency_overrides[get_optional_email_sender] = lambda: None
    app.dependency_overrides[get_one_time_tokens] = lambda: None

    # The route looks the function up on the module at call time, so patching
    # the module is enough and the real provisioning stays out of this test.
    monkeypatch.setattr(service, "provision_tenant", provision)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(f"{API_PREFIX}/signup", json=body)
    return response, log, asked


_SIGNUP: dict[str, object] = {
    "organization_name": "Acme Inc",
    "email": "owner@acme.test",
    "password": "correct-horse-battery",
}


async def test_the_tenant_is_committed_before_its_session_is_minted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-71 follow-on: the session store is not in the request's transaction.

    A session written before the rows it names are committed names rows its
    foreign key cannot find, and signup fails on a brand-new account. Order is
    the fix, so order is what this pins.
    """
    response, log, _ = await _sign_up(monkeypatch, _SIGNUP)

    assert response.status_code == 201, response.text  # type: ignore[attr-defined]
    assert "commit" in log.events, "signup never committed the tenant it created"
    assert log.events.index("commit") < log.events.index("session"), (
        f"the session was minted before the tenant was durable: {log.events}"
    )


async def test_signup_records_the_browser_s_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """D23, FR24: the zone the browser reports becomes the owner's, and so the first
    project's, so its days are local and not UTC."""
    response, _, asked = await _sign_up(monkeypatch, {**_SIGNUP, "timezone": "Asia/Kolkata"})

    assert response.status_code == 201, response.text  # type: ignore[attr-defined]
    assert asked["timezone"] == "Asia/Kolkata"


@pytest.mark.parametrize("reported", ["Mars/Olympus_Mons", "../../etc/passwd", "", None])
async def test_a_zone_the_browser_reports_wrongly_signs_up_in_utc(
    monkeypatch: pytest.MonkeyPatch, reported: str | None
) -> None:
    """A browser's zone is a hint, not something a person typed: one this system does
    not know signs up with none, so the project falls back to UTC (FR24), rather than
    refusing a new customer over it."""
    response, _, asked = await _sign_up(monkeypatch, {**_SIGNUP, "timezone": reported})

    assert response.status_code == 201, response.text  # type: ignore[attr-defined]
    assert asked["timezone"] is None
