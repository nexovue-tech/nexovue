"""The public deploy webhook, offline (P1-36).

Two things are proved here, and they are the two that make this endpoint safe to
expose to the internet.

**Nothing unsigned is written.** A missing signature, a wrong one, one over a
body that was altered after signing, and one from another project's secret are
all refused, and the refusal happens before the row is staged — the assertions
check the store stayed empty, not merely that an exception came out.

**Every query carries its tenant in the statement.** The statements are captured
before they reach a database and their bound parameters inspected. A query that
lost its `org_id` predicate would still return the right rows against a real
Postgres if the fixture happened to hold only one tenant's data, so asserting on
the *statement* is what actually proves the filter is there.

`test_deploys_live.py` runs the same operations against real Postgres.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest

from nexovue_api.deploys import queries, service, signatures
from nexovue_api.deploys.queries import WebhookKey
from nexovue_api.errors.exceptions import InvalidRequestError, UnauthenticatedError

ORG_ID = uuid.uuid4()
OTHER_ORG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
OTHER_PROJECT_ID = uuid.uuid4()

# Stands in for `api_keys.key_hash`, which is what the derivation is keyed on.
KEY_HASH = "a" * 64
OTHER_KEY_HASH = "b" * 64

BODY = json.dumps({"version": "1.4.2", "service_name": "checkout"}).encode()


def sign(body: bytes, secret: str) -> str:
    """What GitHub puts in the header, computed the way GitHub computes it."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# --------------------------------------------------------------- the statements


class CapturingSession:
    """Records the statement it was handed and returns nothing.

    Deliberately not a database. The question is what the query asked for, and a
    real store would answer that with data rather than with the query.
    """

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def scalars(self, statement: Any) -> Any:
        self.statements.append(statement)
        return _Empty()

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return None

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return _Empty()

    @property
    def bound_values(self) -> dict[str, Any]:
        compiled = self.statements[-1].compile()
        return dict(compiled.params)


class _Empty:
    def all(self) -> list[Any]:
        return []


def _bound(session: CapturingSession) -> set[Any]:
    return set(session.bound_values.values())


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


async def test_listing_deploys_binds_the_organization(session: CapturingSession) -> None:
    """A list scoped only by project would return another tenant's deploys."""
    await queries.list_deploy_events(session, org_id=ORG_ID, project_id=PROJECT_ID, limit=10)  # type: ignore[arg-type]

    assert ORG_ID in _bound(session)
    assert PROJECT_ID in _bound(session)


async def test_reading_live_keys_binds_the_organization(session: CapturingSession) -> None:
    """These rows derive the secrets. Unscoped, they would hand a caller the
    secret for a project in another organization."""
    await queries.live_keys(session, org_id=ORG_ID, project_id=PROJECT_ID)  # type: ignore[arg-type]

    assert ORG_ID in _bound(session)
    assert PROJECT_ID in _bound(session)


async def test_the_webhook_lookup_binds_the_project_it_was_given(
    session: CapturingSession,
) -> None:
    """The public caller has no organization, so the project row is what supplies
    one. It must at least be the project the URL named."""
    await queries.webhook_keys(session, project_id=PROJECT_ID)  # type: ignore[arg-type]

    assert PROJECT_ID in _bound(session)


async def test_no_query_binds_an_organization_it_was_not_given(
    session: CapturingSession,
) -> None:
    """Guards the test itself: presence would pass even if org_id were ignored."""
    await queries.list_deploy_events(session, org_id=ORG_ID, project_id=PROJECT_ID, limit=10)  # type: ignore[arg-type]

    assert OTHER_ORG_ID not in _bound(session)


# ---------------------------------------------------------------- the signature


class TestSignatureVerification:
    """The header, on its own. Everything the endpoint decides starts here."""

    def test_a_signature_over_these_bytes_is_accepted(self) -> None:
        secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

        assert signatures.signature_matches(
            body=BODY, header_value=sign(BODY, secret), secrets=[secret]
        )

    def test_an_uppercase_digest_is_accepted(self) -> None:
        """Hex is case-insensitive; a client that upper-cases it is not a forgery."""
        secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

        assert signatures.signature_matches(
            body=BODY, header_value=sign(BODY, secret).upper(), secrets=[secret]
        )

    def test_any_of_a_projects_live_secrets_is_accepted(self) -> None:
        """Rotating an ingest key must not break a webhook signed with the other."""
        first = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)
        second = signatures.derive_secret(key_hash=OTHER_KEY_HASH, project_id=PROJECT_ID)

        assert signatures.signature_matches(
            body=BODY, header_value=sign(BODY, second), secrets=[first, second]
        )

    def test_a_missing_signature_is_refused(self) -> None:
        secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

        assert not signatures.signature_matches(body=BODY, header_value=None, secrets=[secret])

    @pytest.mark.parametrize(
        "header_value",
        [
            "",
            "sha256=",
            "deadbeef",
            "sha1=" + "0" * 40,  # the legacy header's algorithm, not accepted
            "sha256=" + "0" * 63,  # one character short of a digest
            "sha256=" + "z" * 64,  # right length, not hex
            "sha256=" + "0" * 64,
        ],
    )
    def test_a_wrong_or_malformed_signature_is_refused(self, header_value: str) -> None:
        secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

        assert not signatures.signature_matches(
            body=BODY, header_value=header_value, secrets=[secret]
        )

    def test_a_body_altered_after_signing_is_refused(self) -> None:
        """The whole point: the signature covers the bytes, not the intent."""
        secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)
        header_value = sign(BODY, secret)
        tampered = BODY.replace(b"1.4.2", b"6.6.6")

        assert signatures.signature_matches(body=BODY, header_value=header_value, secrets=[secret])
        assert not signatures.signature_matches(
            body=tampered, header_value=header_value, secrets=[secret]
        )

    def test_a_project_with_no_live_key_accepts_nothing(self) -> None:
        secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

        assert not signatures.signature_matches(
            body=BODY, header_value=sign(BODY, secret), secrets=[]
        )


class TestSecretDerivation:
    """A shared secret would let any customer forge any other's deploys."""

    def test_two_projects_on_the_same_key_get_different_secrets(self) -> None:
        assert signatures.derive_secret(
            key_hash=KEY_HASH, project_id=PROJECT_ID
        ) != signatures.derive_secret(key_hash=KEY_HASH, project_id=OTHER_PROJECT_ID)

    def test_two_keys_on_the_same_project_get_different_secrets(self) -> None:
        assert signatures.derive_secret(
            key_hash=KEY_HASH, project_id=PROJECT_ID
        ) != signatures.derive_secret(key_hash=OTHER_KEY_HASH, project_id=PROJECT_ID)

    def test_another_projects_secret_does_not_sign_this_projects_webhook(self) -> None:
        """The forgery this endpoint exists to refuse."""
        theirs = signatures.derive_secret(key_hash=OTHER_KEY_HASH, project_id=OTHER_PROJECT_ID)
        ours = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

        assert not signatures.signature_matches(
            body=BODY, header_value=sign(BODY, theirs), secrets=[ours]
        )

    def test_the_secret_is_recognisable_as_ours(self) -> None:
        """A leaked secret in a log or a paste should be identifiable on sight."""
        secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

        assert secret.startswith(signatures.SECRET_PREFIX)
        assert KEY_HASH not in secret  # the derivation is one-way


# ------------------------------------------------------------------ the service


class RecordingSession:
    """Accepts staged rows and hands out the ids Postgres would.

    Enough of a session for the service to run; the live suite proves the real
    statements. What it is here for is the assertion that nothing was staged.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        for instance in self.added:
            if getattr(instance, "id", None) is None:
                instance.id = uuid.uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> RecordingSession:
    """A session whose project has exactly one live ingest key."""
    monkeypatch.setattr(
        queries,
        "webhook_keys",
        _keys_returning([WebhookKey(org_id=ORG_ID, key_hash=KEY_HASH)]),
    )
    return RecordingSession()


def _keys_returning(keys: list[WebhookKey]) -> Any:
    async def _webhook_keys(session: Any, *, project_id: uuid.UUID) -> list[WebhookKey]:
        return keys

    return _webhook_keys


def _deploys(store: RecordingSession) -> list[Any]:
    return [row for row in store.added if type(row).__name__ == "DeployEvent"]


def _audit_actions(store: RecordingSession) -> list[str]:
    return [row.action for row in store.added if type(row).__name__ == "AuditLogEntry"]


async def test_a_correctly_signed_deploy_is_stored(store: RecordingSession) -> None:
    secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

    summary = await service.record_from_webhook(
        store,  # type: ignore[arg-type]
        project_id=PROJECT_ID,
        body=BODY,
        signature=sign(BODY, secret),
    )

    assert summary.version == "1.4.2"
    assert summary.service_name == "checkout"
    assert summary.project_id == PROJECT_ID
    stored = _deploys(store)
    assert len(stored) == 1
    # Tenancy comes from the project's own row, never from the request.
    assert stored[0].org_id == ORG_ID
    assert "deploy.recorded" in _audit_actions(store)


@pytest.mark.parametrize(
    ("signature", "why"),
    [
        (None, "no signature at all"),
        ("sha256=" + "0" * 64, "a wrong signature"),
        ("garbage", "a header that is not a signature"),
    ],
)
async def test_an_unsigned_or_wrongly_signed_deploy_writes_nothing(
    store: RecordingSession, signature: str | None, why: str
) -> None:
    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            store,  # type: ignore[arg-type]
            project_id=PROJECT_ID,
            body=BODY,
            signature=signature,
        )

    assert store.added == [], f"{why} still reached the store"


async def test_a_body_altered_after_signing_writes_nothing(store: RecordingSession) -> None:
    """A proxy, or an attacker, changing the version between signing and arrival."""
    secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)
    header_value = sign(BODY, secret)
    tampered = BODY.replace(b"checkout", b"payments")

    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            store,  # type: ignore[arg-type]
            project_id=PROJECT_ID,
            body=tampered,
            signature=header_value,
        )

    assert store.added == []


async def test_another_projects_secret_cannot_sign_this_projects_deploy(
    store: RecordingSession,
) -> None:
    """A customer holding their own secret must not be able to forge somebody
    else's deploy — the reason the secret is per project rather than global."""
    theirs = signatures.derive_secret(key_hash=OTHER_KEY_HASH, project_id=OTHER_PROJECT_ID)

    with pytest.raises(UnauthenticatedError):
        await service.record_from_webhook(
            store,  # type: ignore[arg-type]
            project_id=PROJECT_ID,
            body=BODY,
            signature=sign(BODY, theirs),
        )

    assert store.added == []


async def test_an_unknown_project_is_refused_in_the_same_words_as_a_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise the endpoint answers "is this project id real?" for anyone."""
    monkeypatch.setattr(queries, "webhook_keys", _keys_returning([]))
    store = RecordingSession()
    secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

    with pytest.raises(UnauthenticatedError) as unknown_project:
        await service.record_from_webhook(
            store,  # type: ignore[arg-type]
            project_id=uuid.uuid4(),
            body=BODY,
            signature=sign(BODY, secret),
        )

    assert unknown_project.value.detail == service.REFUSED_DETAIL
    assert store.added == []


async def test_a_signed_body_that_is_not_a_deploy_is_refused_after_the_signature(
    store: RecordingSession,
) -> None:
    """Parsing happens after verification, so this is a 400 and not a 401."""
    secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)
    body = json.dumps({"version": "  "}).encode()

    with pytest.raises(InvalidRequestError):
        await service.record_from_webhook(
            store,  # type: ignore[arg-type]
            project_id=PROJECT_ID,
            body=body,
            signature=sign(body, secret),
        )

    assert _deploys(store) == []


async def test_a_deploy_without_a_time_is_stamped_on_arrival(store: RecordingSession) -> None:
    """CI that does not send one still produces an orderable event."""
    secret = signatures.derive_secret(key_hash=KEY_HASH, project_id=PROJECT_ID)

    summary = await service.record_from_webhook(
        store,  # type: ignore[arg-type]
        project_id=PROJECT_ID,
        body=BODY,
        signature=sign(BODY, secret),
    )

    assert summary.deployed_at.tzinfo is not None
