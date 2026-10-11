"""The session list and the two-step sign-in, driven through the real app (T64).

In the shape `test_auth_routes.py` established: Postgres and the stores on it are replaced
by dependency overrides, and what is under test is the wiring — which body comes
back, which status, what the cookie does, and what is *absent* from a response.

Three absences are asserted here and each is the point of a design decision:

* a challenge body names nothing about the account it belongs to;
* no cookie is set until the second factor has been proved;
* the secret appears in exactly one response and never again.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import pyotp
import pytest
from environment import TEST_ENVIRONMENT
from fake_stores import FakeSecondFactorStore, use_fake_second_factors
from fastapi import FastAPI
from nexovue_platform.checks.sealing import ConfiguredKeys

from nexovue_api.auth.clients import ClientDescription
from nexovue_api.auth.credentials import hash_password
from nexovue_api.auth.dependencies import get_credential_box, get_optional_credential_box
from nexovue_api.auth.encryption import credential_box, generate_key
from nexovue_api.auth.sessions import SESSION_COOKIE_NAME, ActiveSession, Session
from nexovue_api.auth.totp import TOTP_INTERVAL_SECONDS
from nexovue_api.data.postgres.models import Organization, User, UserRecoveryCode
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
from nexovue_api.settings import API_PREFIX, Settings

PASSWORD: Final = "correct-horse-battery"
AUTH: Final = f"{API_PREFIX}/auth"
PROFILE: Final = f"{API_PREFIX}/profile"

CHROME_ON_MAC: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def _settings() -> Settings:
    return Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]


class FakeSessionStore:
    """The store's whole interface, including the per-user index, in dicts."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self._minted = 0

    async def create(
        self, *, user_id: str, org_id: str, client: ClientDescription | None = None
    ) -> tuple[str, Session]:
        self._minted += 1
        token = f"token-{self._minted}"
        session = Session(
            user_id=user_id,
            org_id=org_id,
            created_at=datetime.now(UTC) + timedelta(seconds=self._minted),
            client=client or ClientDescription.unknown(),
        )
        self.sessions[token] = session
        return token, session

    async def get(self, token: str) -> Session | None:
        return self.sessions.get(token)

    async def revoke(self, token: str) -> None:
        self.sessions.pop(token, None)

    async def list_for(self, *, org_id: str, user_id: str) -> list[ActiveSession]:
        found = [
            ActiveSession(
                id=_fingerprint(token),
                created_at=session.created_at,
                last_seen_at=session.last_used_at,
                client=session.client,
            )
            for token, session in self.sessions.items()
            if session.user_id == user_id and session.org_id == org_id
        ]
        return sorted(found, key=lambda entry: entry.created_at, reverse=True)

    async def revoke_one_of(self, *, org_id: str, user_id: str, session_id: str) -> bool:
        for token, session in list(self.sessions.items()):
            if (
                _fingerprint(token) == session_id
                and session.user_id == user_id
                and session.org_id == org_id
            ):
                del self.sessions[token]
                return True
        return False


def _fingerprint(token: str) -> str:
    from nexovue_api.auth.sessions import token_fingerprint

    return token_fingerprint(token)


class FakeDb:
    """Answers `load_identity`, the login lookup, and the recovery-code reads."""

    def __init__(self, user: User, organization: Organization) -> None:
        self.user = user
        self.organization = organization
        self.written: list[Any] = []
        self.codes: list[UserRecoveryCode] = []

    def add(self, entity: Any) -> None:
        self.written.append(entity)

    def add_all(self, entities: Any) -> None:
        self.codes.extend(entities)

    async def get(self, model: Any, identifier: Any) -> Any:
        return self.user if identifier == self.user.id else None

    async def execute(self, statement: Any) -> Any:
        if not statement.is_select:
            self.codes.clear()
            return _Result(None, [])
        # The identity lookups select two entities; the code lookup selects one.
        if len(statement.column_descriptions) == 2:
            return _Result((self.user, self.organization), [])
        return _Result(None, [row for row in self.codes if row.used_at is None])

    async def scalar(self, statement: Any) -> Any:
        return len([row for row in self.codes if row.used_at is None])

    async def commit(self) -> None:
        """Nothing to commit; the fake objects are the rows."""

    async def flush(self) -> None:
        """No constraint to violate here; the live suite owns that half."""


class _Result:
    def __init__(self, row: Any, rows: list[UserRecoveryCode]) -> None:
        self._row = row
        self._rows = rows

    def first(self) -> Any:
        return self._row

    def scalars(self) -> list[UserRecoveryCode]:
        return self._rows


@pytest.fixture
def organization() -> Organization:
    organization = Organization(slug="acme", name="Acme Inc")
    organization.id = uuid.uuid4()
    return organization


@pytest.fixture
def user(organization: Organization) -> User:
    user = User(
        org_id=organization.id,
        email="ada@analytical.test",
        name="Ada",
        password_hash=hash_password(PASSWORD),
        role="owner",
        # What the column holds for every stored user; an unsaved one has no default yet.
        failed_logins=0,
    )
    user.id = uuid.uuid4()
    return user


@pytest.fixture
def store() -> FakeSessionStore:
    return FakeSessionStore()


@pytest.fixture
def db(user: User, organization: Organization) -> FakeDb:
    return FakeDb(user, organization)


@pytest.fixture
def key() -> str:
    return generate_key()


@pytest.fixture
def app(db: FakeDb, store: FakeSessionStore, key: str) -> FastAPI:
    from nexovue_api.main import create_app

    settings = _settings()
    built = create_app(settings)

    async def db_override() -> AsyncIterator[Any]:
        yield db

    box = credential_box(ConfiguredKeys(key))

    built.dependency_overrides[get_app_settings] = lambda: settings
    built.dependency_overrides[get_db_session] = db_override
    built.dependency_overrides[get_session_store] = lambda: store
    built.dependency_overrides[get_credential_box] = lambda: box
    built.dependency_overrides[get_optional_credential_box] = lambda: box
    use_fake_second_factors(built)
    return built


@pytest.fixture
def second_factors(app: FastAPI) -> FakeSecondFactorStore:
    from nexovue_api.auth.dependencies import get_second_factor_store

    return app.dependency_overrides[get_second_factor_store]()  # type: ignore[no-any-return]


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"user-agent": CHROME_ON_MAC},
    )


async def _sign_in(client: httpx.AsyncClient, user: User) -> httpx.Response:
    return await client.post(f"{AUTH}/login", json={"email": user.email, "password": PASSWORD})


def _next_code(secret: str) -> str:
    """The code of the step after this one.

    Needed because enrolment spends the code that confirmed it: a code is valid
    for its whole step, so one used to arm the factor must not also be usable to
    pass the first challenge. `test_the_enrolment_code_cannot_also_sign_in`
    asserts that directly; every other test here simply moves on a step.
    """
    return pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=TOTP_INTERVAL_SECONDS))


async def _enrol(client: httpx.AsyncClient) -> tuple[str, list[str]]:
    """Enrol the signed-in caller and return the secret and recovery codes."""
    offered = await client.post(f"{PROFILE}/two-factor/enrol", json={"current_password": PASSWORD})
    assert offered.status_code == 200, offered.text
    secret = offered.json()["secret"]

    confirmed = await client.post(
        f"{PROFILE}/two-factor/confirm", json={"code": pyotp.TOTP(secret).now()}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


# ------------------------------------------------------------- the session list


async def test_the_list_describes_a_device_without_describing_the_person(
    app: FastAPI, user: User
) -> None:
    """The two fields a customer recognises a session by, and no more. What is
    *not* here is the point: no raw User-Agent, no exact address."""
    async with _client(app) as client:
        await _sign_in(client, user)
        listed = await client.get(f"{PROFILE}/sessions")

    assert listed.status_code == 200
    (entry,) = listed.json()
    assert entry["client"] == "Chrome on macOS"
    assert set(entry) == {"id", "created_at", "last_seen_at", "client", "network", "current"}
    assert "537.36" not in listed.text


async def test_the_caller_s_own_session_is_the_one_marked_current(
    app: FastAPI, user: User, store: FakeSessionStore
) -> None:
    """Computed from the request's own token. A client that could nominate it
    could talk somebody out of ending the session that is not theirs."""
    async with _client(app) as client:
        await _sign_in(client, user)
        # A second sign-in from somewhere else, as another device would be.
        await store.create(user_id=str(user.id), org_id=str(user.org_id))
        listed = (await client.get(f"{PROFILE}/sessions")).json()

    assert [entry["current"] for entry in listed].count(True) == 1


async def test_the_list_holds_only_the_callers_own_sessions(
    app: FastAPI, user: User, store: FakeSessionStore
) -> None:
    """**The scoping claim, asserted.** Another account's session exists in the
    same store and must not appear."""
    async with _client(app) as client:
        await _sign_in(client, user)
        await store.create(user_id=str(uuid.uuid4()), org_id=str(uuid.uuid4()))
        listed = (await client.get(f"{PROFILE}/sessions")).json()

    assert len(listed) == 1


async def test_ending_one_session_leaves_the_others_alone(
    app: FastAPI, user: User, store: FakeSessionStore
) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        other_token, _ = await store.create(user_id=str(user.id), org_id=str(user.org_id))
        listed = (await client.get(f"{PROFILE}/sessions")).json()
        target = next(entry for entry in listed if not entry["current"])

        ended = await client.delete(f"{PROFILE}/sessions/{target['id']}")
        remaining = (await client.get(f"{PROFILE}/sessions")).json()

    assert ended.status_code == 204
    assert other_token not in store.sessions
    assert [entry["current"] for entry in remaining] == [True]


async def test_another_accounts_session_cannot_be_ended_and_is_not_admitted_to_exist(
    app: FastAPI, user: User, store: FakeSessionStore
) -> None:
    """404 for an id that is not the caller's, whether it belongs to somebody
    else or to nothing at all — so the refusal is not a way to learn that a
    stranger's session is live."""
    async with _client(app) as client:
        await _sign_in(client, user)
        stranger_token, _ = await store.create(user_id=str(uuid.uuid4()), org_id=str(uuid.uuid4()))

        stranger = await client.delete(f"{PROFILE}/sessions/{_fingerprint(stranger_token)}")
        invented = await client.delete(f"{PROFILE}/sessions/{'a' * 64}")

    assert stranger.status_code == invented.status_code == 404
    assert stranger.json()["detail"] == invented.json()["detail"]
    assert stranger_token in store.sessions


async def test_the_session_list_refuses_an_anonymous_caller(app: FastAPI) -> None:
    async with _client(app) as client:
        listed = await client.get(f"{PROFILE}/sessions")
        ended = await client.delete(f"{PROFILE}/sessions/{'a' * 64}")

    assert listed.status_code == ended.status_code == 401


# ------------------------------------------------------------------- enrolling


async def test_the_secret_is_returned_once_and_by_nothing_else(app: FastAPI, user: User) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        secret, _ = await _enrol(client)
        state = await client.get(f"{PROFILE}/two-factor")

    assert state.json()["enabled"] is True
    assert secret not in state.text


async def test_enrolment_is_refused_without_the_current_password(
    app: FastAPI, user: User, second_factors: FakeSecondFactorStore
) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        refused = await client.post(
            f"{PROFILE}/two-factor/enrol", json={"current_password": "not-the-password"}
        )

    assert refused.status_code == 422
    assert second_factors.enrolments == {}


async def test_a_deployment_with_no_key_refuses_enrolment_rather_than_storing_plaintext(
    app: FastAPI, user: User
) -> None:
    """503, and the account is untouched. The variable name stays out of the body."""
    app.dependency_overrides[get_credential_box] = lambda: credential_box(ConfiguredKeys(""))

    async with _client(app) as client:
        await _sign_in(client, user)
        refused = await client.post(
            f"{PROFILE}/two-factor/enrol", json={"current_password": PASSWORD}
        )

    assert refused.status_code == 503
    assert "CREDENTIAL_ENCRYPTION_KEY" not in refused.text


async def test_the_recovery_codes_are_shown_once_and_counted_afterwards(
    app: FastAPI, user: User
) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        _, codes = await _enrol(client)
        state = await client.get(f"{PROFILE}/two-factor")

    assert len(codes) == 10
    assert state.json()["recovery_codes_remaining"] == 10
    assert not any(code in state.text for code in codes)


# --------------------------------------------------------------- signing in


async def test_a_password_alone_no_longer_signs_in_once_the_factor_is_on(
    app: FastAPI, user: User
) -> None:
    """**The challenge body names nothing about the account.** Somebody who has
    guessed a password learns that they guessed it, and no more."""
    async with _client(app) as client:
        await _sign_in(client, user)
        await _enrol(client)
        client.cookies.clear()

        answered = await _sign_in(client, user)

    assert answered.status_code == 200
    body = answered.json()
    assert body["second_factor_required"] is True
    assert set(body) == {"second_factor_required", "challenge"}
    assert user.email not in answered.text
    assert SESSION_COOKIE_NAME not in answered.cookies


async def test_the_code_completes_the_sign_in_and_sets_the_cookie(app: FastAPI, user: User) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        secret, _ = await _enrol(client)
        client.cookies.clear()

        challenge = (await _sign_in(client, user)).json()["challenge"]
        signed_in = await client.post(
            f"{AUTH}/login/second-factor",
            json={"challenge": challenge, "code": _next_code(secret)},
        )

    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["email"] == user.email
    assert SESSION_COOKIE_NAME in signed_in.cookies


async def test_the_enrolment_code_cannot_also_sign_in(app: FastAPI, user: User) -> None:
    """**Replay, at the one moment it is easiest to get wrong.** The code that
    confirmed the enrolment is still valid for the rest of its step, and it must
    not pass the challenge that follows."""
    async with _client(app) as client:
        await _sign_in(client, user)
        offered = await client.post(
            f"{PROFILE}/two-factor/enrol", json={"current_password": PASSWORD}
        )
        assert offered.status_code == 200, offered.text
        secret = offered.json()["secret"]

        # Captured once and reused for the replay below, never re-derived: a
        # second `pyotp.TOTP(secret).now()` call can land in the next 30s step
        # and silently assert about a code that was never submitted to confirm.
        used_to_enrol = pyotp.TOTP(secret).now()
        confirmed = await client.post(f"{PROFILE}/two-factor/confirm", json={"code": used_to_enrol})
        assert confirmed.status_code == 200, confirmed.text
        client.cookies.clear()

        challenge = (await _sign_in(client, user)).json()["challenge"]
        replayed = await client.post(
            f"{AUTH}/login/second-factor",
            json={"challenge": challenge, "code": used_to_enrol},
        )

    assert replayed.status_code == 401
    assert SESSION_COOKIE_NAME not in replayed.cookies


async def test_a_recovery_code_completes_the_sign_in_too(app: FastAPI, user: User) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        _, codes = await _enrol(client)
        client.cookies.clear()

        challenge = (await _sign_in(client, user)).json()["challenge"]
        signed_in = await client.post(
            f"{AUTH}/login/second-factor", json={"challenge": challenge, "code": codes[0]}
        )

    assert signed_in.status_code == 200
    assert SESSION_COOKIE_NAME in signed_in.cookies


async def test_every_way_the_second_step_can_fail_says_the_same_thing(
    app: FastAPI, user: User
) -> None:
    """A wrong code, a challenge that never existed, and one that has expired.
    Distinguishing them would tell somebody holding a stolen password which half
    of the attack is landing."""
    async with _client(app) as client:
        await _sign_in(client, user)
        await _enrol(client)
        client.cookies.clear()

        challenge = (await _sign_in(client, user)).json()["challenge"]
        wrong_code = await client.post(
            f"{AUTH}/login/second-factor", json={"challenge": challenge, "code": "000000"}
        )
        unknown_challenge = await client.post(
            f"{AUTH}/login/second-factor", json={"challenge": "never-issued", "code": "000000"}
        )

    assert wrong_code.status_code == unknown_challenge.status_code == 401
    assert wrong_code.json()["detail"] == unknown_challenge.json()["detail"]
    assert wrong_code.json()["type"] == unknown_challenge.json()["type"]
    assert SESSION_COOKIE_NAME not in wrong_code.cookies


async def test_a_challenge_is_spent_by_enough_wrong_codes(app: FastAPI, user: User) -> None:
    """The rate limiter bounds how fast attempts arrive; this bounds how many one
    password submission is worth."""
    from nexovue_api.auth.totp import MAX_CHALLENGE_ATTEMPTS

    async with _client(app) as client:
        await _sign_in(client, user)
        secret, _ = await _enrol(client)
        client.cookies.clear()

        challenge = (await _sign_in(client, user)).json()["challenge"]
        for _ in range(MAX_CHALLENGE_ATTEMPTS):
            await client.post(
                f"{AUTH}/login/second-factor", json={"challenge": challenge, "code": "000000"}
            )

        with_the_right_code = await client.post(
            f"{AUTH}/login/second-factor",
            json={"challenge": challenge, "code": _next_code(secret)},
        )

    assert with_the_right_code.status_code == 401


async def test_a_spent_challenge_cannot_be_replayed(app: FastAPI, user: User) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        secret, _ = await _enrol(client)
        client.cookies.clear()

        challenge = (await _sign_in(client, user)).json()["challenge"]
        code = _next_code(secret)
        first = await client.post(
            f"{AUTH}/login/second-factor", json={"challenge": challenge, "code": code}
        )
        again = await client.post(
            f"{AUTH}/login/second-factor", json={"challenge": challenge, "code": code}
        )

    assert first.status_code == 200
    assert again.status_code == 401


# --------------------------------------------------------------- turning it off


async def test_turning_it_off_needs_the_current_password(app: FastAPI, user: User) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        await _enrol(client)

        refused = await client.post(
            f"{PROFILE}/two-factor/disable", json={"current_password": "wrong"}
        )
        state = await client.get(f"{PROFILE}/two-factor")

    assert refused.status_code == 422
    assert state.json()["enabled"] is True


async def test_turning_it_off_restores_a_one_step_sign_in(app: FastAPI, user: User) -> None:
    async with _client(app) as client:
        await _sign_in(client, user)
        await _enrol(client)

        removed = await client.post(
            f"{PROFILE}/two-factor/disable", json={"current_password": PASSWORD}
        )
        client.cookies.clear()
        signed_in = await _sign_in(client, user)

    assert removed.status_code == 204
    assert "second_factor_required" not in signed_in.text
    assert SESSION_COOKIE_NAME in signed_in.cookies
