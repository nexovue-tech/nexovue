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
from dataclasses import dataclass
from typing import Any, Final

import pytest

from nexovue_api.deploys import queries, service, signatures
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, UnauthenticatedError

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


async def test_revoking_a_secret_binds_the_organization(session: CapturingSession) -> None:
    """Unscoped, a revoke by id alone would let any caller withdraw another
    organization's secret and stop their deploys arriving."""
    secret_id = uuid.uuid4()

    await queries.revoke_webhook_secret(session, org_id=ORG_ID, secret_id=secret_id)  # type: ignore[arg-type]

    assert ORG_ID in _bound(session)
    assert secret_id in _bound(session)


async def test_the_webhook_lookup_binds_the_project_it_was_given(
    session: CapturingSession,
) -> None:
    """The public caller has no organization, so the project row is what supplies
    one. It must at least be the project the URL named."""
    await queries.live_webhook_secrets(session, project_id=PROJECT_ID)  # type: ignore[arg-type]

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
        secret = LIVE_SECRET

        assert signatures.signature_matches(
            body=BODY, header_value=sign(BODY, secret), secrets=[secret]
        )

    def test_an_uppercase_digest_is_accepted(self) -> None:
        """Hex is case-insensitive; a client that upper-cases it is not a forgery."""
        secret = LIVE_SECRET

        assert signatures.signature_matches(
            body=BODY, header_value=sign(BODY, secret).upper(), secrets=[secret]
        )

    def test_any_of_a_projects_live_secrets_is_accepted(self) -> None:
        """A rotation overlaps: the new secret is minted, CI is updated, and only
        then is the old one revoked. Both are live in between, so a delivery
        signed with either has to be accepted."""
        first = signatures.new_secret()
        second = signatures.new_secret()

        assert signatures.signature_matches(
            body=BODY, header_value=sign(BODY, second), secrets=[first, second]
        )

    def test_a_missing_signature_is_refused(self) -> None:
        secret = LIVE_SECRET

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
        secret = LIVE_SECRET

        assert not signatures.signature_matches(
            body=BODY, header_value=header_value, secrets=[secret]
        )

    def test_a_body_altered_after_signing_is_refused(self) -> None:
        """The whole point: the signature covers the bytes, not the intent."""
        secret = LIVE_SECRET
        header_value = sign(BODY, secret)
        tampered = BODY.replace(b"1.4.2", b"6.6.6")

        assert signatures.signature_matches(body=BODY, header_value=header_value, secrets=[secret])
        assert not signatures.signature_matches(
            body=tampered, header_value=header_value, secrets=[secret]
        )

    def test_a_project_with_no_live_key_accepts_nothing(self) -> None:
        secret = LIVE_SECRET

        assert not signatures.signature_matches(
            body=BODY, header_value=sign(BODY, secret), secrets=[]
        )


async def test_a_replayed_delivery_is_refused(
    store: RecordingSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correctly signed request stays valid forever, because the scheme has no
    timestamp. The delivery id is the only thing that tells a captured request
    apart from a genuine one, so a second arrival of the same id is refused
    rather than stored twice."""
    monkeypatch.setattr(queries, "delivery_already_recorded", _seen(True))

    with pytest.raises(ConflictError):
        await service.record_from_webhook(
            store,  # type: ignore[arg-type]
            project_id=PROJECT_ID,
            body=BODY,
            signature=sign(BODY, LIVE_SECRET),
            delivery_id="a-delivery-seen-before",
        )

    assert _deploys(store) == [], "a replay must write nothing"


async def test_a_delivery_id_is_stored_so_the_next_replay_can_be_caught(
    store: RecordingSession,
) -> None:
    await service.record_from_webhook(
        store,  # type: ignore[arg-type]
        project_id=PROJECT_ID,
        body=BODY,
        signature=sign(BODY, LIVE_SECRET),
        delivery_id="delivery-1",
    )

    assert _deploys(store)[0].delivery_id == "delivery-1"


async def test_a_sender_without_a_delivery_id_still_works(store: RecordingSession) -> None:
    """Not every CI system sends one. It gets no replay protection, which the
    service says out loud rather than silently refusing the deploy."""
    await service.record_from_webhook(
        store,  # type: ignore[arg-type]
        project_id=PROJECT_ID,
        body=BODY,
        signature=sign(BODY, LIVE_SECRET),
    )

    assert _deploys(store)[0].delivery_id is None


class TestMintedSecrets:
    """A shared secret would let any customer forge any other's deploys."""

    def test_every_secret_is_unique(self) -> None:
        """They come from the OS entropy source, so a collision is not a thing
        that happens — but the property is what the whole scheme rests on."""
        minted = {signatures.new_secret() for _ in range(64)}

        assert len(minted) == 64

    def test_a_secret_is_recognisable_and_long_enough_to_be_a_key(self) -> None:
        secret = LIVE_SECRET

        assert secret.startswith(signatures.SECRET_PREFIX)
        # 32 bytes of entropy, url-safe encoded. An HMAC key shorter than its
        # digest buys nothing.
        assert len(secret) - len(signatures.SECRET_PREFIX) >= 40

    def test_another_projects_secret_does_not_sign_this_projects_webhook(self) -> None:
        """The forgery this endpoint exists to refuse."""
        theirs, ours = signatures.new_secret(), signatures.new_secret()

        assert not signatures.signature_matches(
            body=BODY, header_value=sign(BODY, theirs), secrets=[ours]
        )


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


# The one secret the stubbed project accepts. Shared by the fixture and the
# tests that sign with it, because a test signing with a different secret than
# the store holds would be asserting nothing about the signature check.
LIVE_SECRET: Final = signatures.new_secret()


@dataclass(frozen=True)
class StoredSecret:
    """Stands in for a `project_webhook_secrets` row."""

    org_id: uuid.UUID
    secret: str


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> RecordingSession:
    """A session whose project has exactly one live webhook secret."""
    monkeypatch.setattr(
        queries,
        "live_webhook_secrets",
        _keys_returning([StoredSecret(org_id=ORG_ID, secret=LIVE_SECRET)]),
    )
    # No delivery has been seen before, unless a test says otherwise.
    monkeypatch.setattr(queries, "delivery_already_recorded", _seen(False))
    return RecordingSession()


def _keys_returning(secrets_: list[Any]) -> Any:
    async def _live_secrets(session: Any, *, project_id: uuid.UUID) -> list[Any]:
        return secrets_

    return _live_secrets


def _seen(answer: bool) -> Any:
    async def _already(session: Any, *, project_id: uuid.UUID, delivery_id: str) -> bool:
        return answer

    return _already


def _deploys(store: RecordingSession) -> list[Any]:
    return [row for row in store.added if type(row).__name__ == "DeployEvent"]


def _audit_actions(store: RecordingSession) -> list[str]:
    return [row.action for row in store.added if type(row).__name__ == "AuditLogEntry"]


async def test_a_correctly_signed_deploy_is_stored(store: RecordingSession) -> None:
    secret = LIVE_SECRET

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
    secret = LIVE_SECRET
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
    theirs = signatures.new_secret()

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
    monkeypatch.setattr(queries, "live_webhook_secrets", _keys_returning([]))
    store = RecordingSession()
    secret = LIVE_SECRET

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
    secret = LIVE_SECRET
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
    secret = LIVE_SECRET

    summary = await service.record_from_webhook(
        store,  # type: ignore[arg-type]
        project_id=PROJECT_ID,
        body=BODY,
        signature=sign(BODY, secret),
    )

    assert summary.deployed_at.tzinfo is not None
