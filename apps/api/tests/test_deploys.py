"""The public deploy webhook, offline (P1-36; F28 Step 2).

Three things are proved here, and they are what make this endpoint safe to
expose to the internet.

**Nothing unsigned is written.** A missing signature, a wrong one, one over a
body that was altered after signing, and one from another project's secret are
all refused in the same words, and the refusal happens before anything is
staged — the assertions check nothing was written, not merely that an exception
came out.

**A signed body is checked in every way before anything is stored** (FR6 to FR11):
each refusal names the item and the field, and never becomes a 500.

**Every query carries its tenant in the statement.** The statements are captured
before they reach a database and their bound parameters inspected.

`test_deploys_live.py` runs the writes against real Postgres.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from environment import DEPLOYED_KEYS, TEST_ENVIRONMENT
from nexovue_platform.checks.sealing import (
    DEPLOY_WEBHOOK_PURPOSE,
    ConfiguredKeys,
    Keyring,
    SealedBox,
    decode_key,
    deploy_webhook_owner,
    generate_key,
)
from nexovue_platform.masking import PLACEHOLDER

from nexovue_api.auth.dependencies import Identity
from nexovue_api.auth.dependencies import get_current_identity as current_identity_dependency
from nexovue_api.data.postgres.models import Organization, User
from nexovue_api.deploys import queries, router, service, signatures
from nexovue_api.deploys.schemas import (
    MAX_ITEMS,
    MAX_METADATA_BYTES,
    MAX_URL_LENGTH,
    DeployEventSummary,
)
from nexovue_api.deps import get_app_settings, get_db_session
from nexovue_api.errors.exceptions import (
    DependencyUnavailableError,
    InvalidRequestError,
    UnauthenticatedError,
)
from nexovue_api.ratelimit.limiter import Decision
from nexovue_api.ratelimit.policy import DEPLOYS_ACCEPTED
from nexovue_api.settings import API_PREFIX, Settings

# Fixed, so test ids built from them are the same in every collecting process.
ORG_ID = uuid.UUID("5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001")
OTHER_ORG_ID = uuid.UUID("5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0002")
PROJECT_ID = uuid.UUID("5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100")
SECRET_ID = uuid.UUID("5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0200")
DEPLOY_ID = uuid.UUID("5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0300")

NOW: Final = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
BOX: Final = SealedBox(Keyring(current=decode_key(generate_key())), purpose=DEPLOY_WEBHOOK_PURPOSE)
LIVE_SECRET: Final = signatures.new_secret()


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "delivery_id": "checkout-run-41-attempt-1",
        "service_name": "checkout",
        "version": "1.4.2",
        "deployed_at": NOW.isoformat(),
    }
    item.update(overrides)
    return item


def _body(*items: dict[str, Any], dry_run: bool | None = None) -> bytes:
    payload: dict[str, Any] = {"deploys": list(items) or [_item()]}
    if dry_run is not None:
        payload["dry_run"] = dry_run
    return json.dumps(payload).encode()


BODY: Final = _body()


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
    def bound_values(self) -> set[Any]:
        """Every scalar bound; an `IN` list's members count one by one."""
        found: set[Any] = set()
        for value in self.statements[-1].compile().params.values():
            found |= set(value) if isinstance(value, list | tuple) else {value}
        return found


class _Empty:
    def all(self) -> list[Any]:
        return []

    def __iter__(self) -> Any:
        return iter([])


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


async def test_listing_deploys_binds_the_organization(session: CapturingSession) -> None:
    """A list scoped only by project would return another tenant's deploys."""
    await queries.list_deploy_events(session, org_id=ORG_ID, project_id=PROJECT_ID, limit=10)  # type: ignore[arg-type]

    assert {ORG_ID, PROJECT_ID} <= session.bound_values
    assert OTHER_ORG_ID not in session.bound_values


async def test_revoking_a_secret_binds_the_organization(session: CapturingSession) -> None:
    """Unscoped, a revoke by id alone would let any caller withdraw another
    organization's secret and stop their deploys arriving."""
    await queries.revoke_webhook_secret(session, org_id=ORG_ID, secret_id=SECRET_ID)  # type: ignore[arg-type]

    assert {ORG_ID, SECRET_ID} <= session.bound_values


async def test_the_webhook_lookup_binds_the_project_it_was_given(
    session: CapturingSession,
) -> None:
    """The public caller has no organization, so the project row is what supplies
    one. It must at least be the project the URL named."""
    await queries.live_webhook_secrets(session, project_id=PROJECT_ID)  # type: ignore[arg-type]

    assert PROJECT_ID in session.bound_values


async def test_a_repeat_is_looked_up_inside_the_organization(session: CapturingSession) -> None:
    await queries.recorded_deliveries(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        delivery_ids=["a"],
    )

    assert {ORG_ID, PROJECT_ID} <= session.bound_values


async def test_what_a_secret_signed_is_found_inside_the_organization(
    session: CapturingSession,
) -> None:
    await queries.deploys_signed_since(session, org_id=ORG_ID, secret_id=SECRET_ID, since=NOW)  # type: ignore[arg-type]

    assert {ORG_ID, SECRET_ID} <= session.bound_values


# ---------------------------------------------------------------- the signature


class TestSignatureVerification:
    """The header, on its own. Everything the endpoint decides starts here."""

    def test_a_signature_over_these_bytes_names_the_secret_that_made_it(self) -> None:
        assert (
            signatures.matching_secret(
                body=BODY, header_value=sign(BODY, LIVE_SECRET), secrets=[LIVE_SECRET]
            )
            == 0
        )

    def test_an_uppercase_digest_is_accepted(self) -> None:
        """Hex is case-insensitive; a client that upper-cases it is not a forgery."""
        assert (
            signatures.matching_secret(
                body=BODY, header_value=sign(BODY, LIVE_SECRET).upper(), secrets=[LIVE_SECRET]
            )
            == 0
        )

    def test_either_of_a_rotations_live_secrets_is_accepted(self) -> None:
        """A rotation overlaps: the new secret is minted, CI is updated, and only
        then is the old one revoked. Both are live in between (AC50)."""
        first, second = signatures.new_secret(), signatures.new_secret()

        assert (
            signatures.matching_secret(
                body=BODY, header_value=sign(BODY, second), secrets=[first, second]
            )
            == 1
        )

    @pytest.mark.parametrize(
        "header_value",
        [
            None,
            "",
            "sha256=",
            "deadbeef",
            "sha1=" + "0" * 40,  # the legacy header's algorithm, not accepted
            "sha256=" + "0" * 63,  # one character short of a digest
            "sha256=" + "z" * 64,  # right length, not hex
            "sha256=" + "0" * 64,
        ],
    )
    def test_a_missing_wrong_or_malformed_signature_is_refused(
        self, header_value: str | None
    ) -> None:
        assert (
            signatures.matching_secret(body=BODY, header_value=header_value, secrets=[LIVE_SECRET])
            is None
        )

    def test_a_body_altered_after_signing_is_refused(self) -> None:
        tampered = BODY.replace(b"1.4.2", b"6.6.6")

        assert (
            signatures.matching_secret(
                body=tampered, header_value=sign(BODY, LIVE_SECRET), secrets=[LIVE_SECRET]
            )
            is None
        )

    def test_a_project_with_no_live_secret_accepts_nothing(self) -> None:
        assert (
            signatures.matching_secret(body=BODY, header_value=sign(BODY, LIVE_SECRET), secrets=[])
            is None
        )


class TestMintedSecrets:
    """A shared secret would let any customer forge any other's deploys."""

    def test_every_secret_is_unique(self) -> None:
        assert len({signatures.new_secret() for _ in range(64)}) == 64

    def test_a_secret_is_recognisable_and_long_enough_to_be_a_key(self) -> None:
        assert LIVE_SECRET.startswith(signatures.SECRET_PREFIX)
        # 32 bytes of entropy, url-safe encoded. An HMAC key shorter than its
        # digest buys nothing.
        assert len(LIVE_SECRET) - len(signatures.SECRET_PREFIX) >= 40

    def test_only_a_secrets_last_characters_are_kept_to_show(self) -> None:
        hint = signatures.secret_hint(LIVE_SECRET)

        assert LIVE_SECRET.endswith(hint)
        assert len(hint) == signatures.HINT_LENGTH


# ------------------------------------------------------------- what is checked


def _refusal(body: bytes) -> tuple[int | None, str | None, str]:
    with pytest.raises(service._RefusedBodyError) as refused:
        service._parse(body, now=NOW)
    return refused.value.position, refused.value.field, refused.value.detail


def test_a_whole_batch_parses_with_its_defaults() -> None:
    batch = service._parse(
        _body(_item(), _item(delivery_id="payments-1", service_name="payments")), now=NOW
    )

    assert [item.service_name for item in batch.deploys] == ["checkout", "payments"]
    assert batch.dry_run is False
    assert batch.deploys[0].state == "finished"
    assert batch.deploys[0].source == "webhook"


@pytest.mark.parametrize(
    ("offset", "accepted"),
    [
        (timedelta(minutes=6), False),
        (timedelta(minutes=5), True),
        (-timedelta(days=7), True),
        (-timedelta(days=8), False),
        (timedelta(0), True),
    ],
)
def test_a_claimed_time_must_sit_near_our_clock(offset: timedelta, accepted: bool) -> None:
    """AC11: six minutes ahead or eight days behind is refused, inside is not. It also
    ends any replay of a capture older than a week (FR8)."""
    body = _body(_item(deployed_at=(NOW + offset).isoformat()))

    if accepted:
        service._parse(body, now=NOW)
        return
    position, field_name, detail = _refusal(body)
    assert (position, field_name) == (1, "deployed_at")
    assert "5 minutes ahead" in detail and "7 days behind" in detail


@pytest.mark.parametrize(
    ("overrides", "field_name"),
    [
        ({"commit_sha": "abc123"}, "commit_sha"),
        ({"commit_sha": "z" * 40}, "commit_sha"),
        ({"url": "http://example.com/commit/1"}, "url"),
        ({"url": "https://user:pass@example.com/c"}, "url"),
        ({"url": "https://user@example.com/c"}, "url"),
        ({"url": "https://example.com/c?token=1"}, "url"),
        ({"url": "https://example.com/c#frag"}, "url"),
        ({"url": "https://example.com:99999/c"}, "url"),
        ({"url": "https:///nohost"}, "url"),
        ({"url": "https://example.com/" + "a" * MAX_URL_LENGTH}, "url"),
        ({"metadata": {"nested": {"a": "b"}}}, "metadata"),
        ({"metadata": {"count": 3}}, "metadata"),
        ({"metadata": {"k": "v" * MAX_METADATA_BYTES}}, "metadata"),
        ({"metadata": ["a"]}, "metadata"),
        ({"delivery_id": "d" * 201}, "delivery_id"),
        ({"delivery_id": ""}, "delivery_id"),
        ({"version": "__other__"}, "version"),
        ({"service_name": "__other__"}, "service_name"),
        ({"version": "v1‮1v"}, "version"),
        ({"service_name": "check\U000e0041out"}, "service_name"),
        ({"version": "v1\x00"}, "version"),
        ({"delivery_id": "d\x00"}, "delivery_id"),
        ({"source": "ci\x00"}, "source"),
        ({"url": "https://example.com/\x00"}, "url"),
        ({"metadata": {"k\x00": "v"}}, "metadata"),
        ({"metadata": {"k": "v\x00"}}, "metadata"),
        ({"state": "exploded"}, "state"),
        ({"deployed_at": "2026-11-08T12:00:00"}, "deployed_at"),
        ({"version": "v" * 201}, "version"),
    ],
)
def test_every_bad_field_names_its_item_and_field(
    overrides: dict[str, Any], field_name: str
) -> None:
    """AC12: each is a 422 naming item and field, never a 500. The bad item is second,
    so the position is the item's and not the first."""
    position, named, detail = _refusal(_body(_item(), {**_item(delivery_id="two"), **overrides}))

    assert (position, named) == (2, field_name)
    assert detail.startswith(f"Item 2, field `{field_name}`")


NAME_CASES: Final = [
    case
    for case in json.loads(
        (
            Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/release-names.json"
        ).read_text()
    )["cases"]
    # The empty string is a span with no version; the webhook's own length rule refuses it.
    if case["value"]
]


@pytest.mark.parametrize("case", NAME_CASES, ids=[case["name"] for case in NAME_CASES])
@pytest.mark.parametrize("field_name", ["version", "service_name"])
def test_the_door_refuses_what_the_worker_folds(case: dict[str, Any], field_name: str) -> None:
    """One name check at both doors (FR9): the cases the worker's fold is held to."""
    body = _body(_item(**{field_name: case["value"]}))

    if case["allowed"]:
        service._parse(body, now=NOW)
        return
    position, named, _ = _refusal(body)
    assert (position, named) == (1, field_name)


def test_a_misspelt_field_is_refused_rather_than_dropped() -> None:
    """Otherwise a typo'd `commit_sha` would vanish and the sender never know."""
    position, named, detail = _refusal(_body(_item(comit_sha="a" * 40)))

    assert (position, named) == (1, service.UNKNOWN_FIELD)
    assert "check its spelling" in detail


def test_the_same_delivery_id_twice_in_one_body_is_refused() -> None:
    """AC10: one id for two deploys would store one and answer the other as a repeat."""
    position, named, detail = _refusal(_body(_item(), _item(service_name="payments")))

    assert (position, named) == (2, "delivery_id")
    assert "item 1" in detail


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'"a string"', json.dumps({"deploys": []}).encode(), b"{}"],
)
def test_a_body_that_is_not_a_batch_is_refused_without_naming_an_item(body: bytes) -> None:
    position, _, _ = _refusal(body)

    assert position is None


def test_more_than_the_largest_batch_is_refused() -> None:
    items = [_item(delivery_id=f"d-{n}") for n in range(MAX_ITEMS + 1)]

    position, named, _ = _refusal(_body(*items))

    assert (position, named) == (None, "deploys")


def test_a_commit_is_stored_in_lower_case() -> None:
    batch = service._parse(_body(_item(commit_sha="ABCDEF" + "0" * 34)), now=NOW)

    assert batch.deploys[0].commit_sha == "abcdef" + "0" * 34


def test_a_hundred_items_with_every_field_at_its_limit_fit_the_body_cap() -> None:
    """AC8's first half: the largest legitimate batch fits the 512 KiB cap."""
    metadata = {"k": "v" * (MAX_METADATA_BYTES - len('{"k":""}'))}
    items = [
        _item(
            delivery_id=f"{n:03d}" + "d" * 197,
            service_name=f"{n:03d}" + "s" * 197,
            version="v" * 200,
            state="rolled_back",
            commit_sha="a" * 64,
            url="https://example.com/" + "u" * (MAX_URL_LENGTH - len("https://example.com/")),
            source="s" * 64,
            metadata=metadata,
        )
        for n in range(MAX_ITEMS)
    ]
    body = _body(*items)

    assert len(body) <= router.MAX_BODY_BYTES
    assert len(service._parse(body, now=NOW).deploys) == MAX_ITEMS


# ------------------------------------------------------------------ the service


@dataclass
class StoredSecret:
    """Stands in for a `project_webhook_secrets` row, sealed as the service seals one."""

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    sealed_secret: bytes


def _sealed(secret: str, *, secret_id: uuid.UUID = SECRET_ID) -> StoredSecret:
    return StoredSecret(
        id=secret_id,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        sealed_secret=BOX.seal(secret, owner=deploy_webhook_owner(str(PROJECT_ID), str(secret_id))),
    )


@dataclass
class Recorded:
    """What the stubbed queries were asked to write, and how often the session committed."""

    used: list[uuid.UUID] = field(default_factory=list)
    refusals: list[tuple[int | None, str | None]] = field(default_factory=list)
    checks: int = 0
    commits: int = 0
    charged: list[int] = field(default_factory=list)

    @property
    def wrote_anything(self) -> bool:
        return bool(self.used or self.refusals or self.checks)


class StubSession:
    def __init__(self, recorded: Recorded) -> None:
        self._recorded = recorded

    async def commit(self) -> None:
        self._recorded.commits += 1


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """A project with one live, sealed secret, and every write the service makes recorded."""
    found = Recorded()

    async def live(session: Any, *, project_id: uuid.UUID) -> list[Any]:
        return [_sealed(LIVE_SECRET)] if project_id == PROJECT_ID else []

    async def used(session: Any, *, org_id: uuid.UUID, secret_id: uuid.UUID) -> None:
        found.used.append(secret_id)

    async def refused(session: Any, *, position: Any, field: Any, **_: Any) -> None:
        found.refusals.append((position, field))

    async def check(session: Any, **_: Any) -> None:
        found.checks += 1

    async def known(session: Any, **_: Any) -> set[str]:
        return set()

    async def seen(session: Any, **_: Any) -> dict[str, list[str]]:
        return {}

    monkeypatch.setattr(queries, "live_webhook_secrets", live)
    monkeypatch.setattr(queries, "mark_secret_used", used)
    monkeypatch.setattr(queries, "record_refusal", refused)
    monkeypatch.setattr(queries, "record_check", check)
    monkeypatch.setattr(queries, "known_services", known)
    monkeypatch.setattr(queries, "seen_versions", seen)
    return found


async def _receive(
    recorded: Recorded,
    *,
    body: bytes = BODY,
    signature: str | None = None,
    project_id: uuid.UUID = PROJECT_ID,
) -> Any:
    async def charge(cost: int) -> Decision:
        recorded.charged.append(cost)
        return Decision(allowed=True, quota=DEPLOYS_ACCEPTED, remaining=0, reset_seconds=1)

    return await service.record_from_webhook(
        StubSession(recorded),  # type: ignore[arg-type]
        project_id=project_id,
        body=body,
        signature=signature if signature is not None else sign(body, LIVE_SECRET),
        box=BOX,
        charge=charge,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("signature", "why"),
    [
        ("", "no signature at all"),
        ("sha256=" + "0" * 64, "a wrong signature"),
        ("garbage", "a header that is not a signature"),
        (sign(BODY, signatures.new_secret()), "another project's secret"),
        (sign(BODY.replace(b"checkout", b"payments"), LIVE_SECRET), "a body altered after"),
    ],
    # By reason: a signature over a fresh secret differs in each collecting process.
    ids=lambda value: value if " " in str(value) else "",
)
async def test_an_unsigned_or_wrongly_signed_request_writes_nothing(
    recorded: Recorded, signature: str, why: str
) -> None:
    """AC6: every refusal holds, in the same words, writing nothing — not a refusal, not
    a last use, not a check. Recording an unsigned refusal would let anyone fill it."""
    with pytest.raises(UnauthenticatedError) as refused:
        await _receive(recorded, signature=signature)

    assert refused.value.detail == service.REFUSED_DETAIL
    assert not recorded.wrote_anything, f"{why} still wrote something"
    assert recorded.commits == 0


async def test_an_unknown_project_is_refused_in_the_same_words_as_a_bad_signature(
    recorded: Recorded,
) -> None:
    """Otherwise the endpoint answers "is this project id real?" for anyone."""
    with pytest.raises(UnauthenticatedError) as refused:
        await _receive(recorded, project_id=uuid.uuid4())

    assert refused.value.detail == service.REFUSED_DETAIL
    assert not recorded.wrote_anything


async def test_a_secret_that_cannot_be_opened_is_no_match(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sealed under a key this deployment no longer holds: refused in the same words,
    and logged for the operator by id alone (design, *Failure handling*)."""
    stranger = SealedBox(
        Keyring(current=decode_key(generate_key())), purpose=DEPLOY_WEBHOOK_PURPOSE
    )
    unreadable = StoredSecret(
        id=SECRET_ID,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        sealed_secret=stranger.seal(
            LIVE_SECRET, owner=deploy_webhook_owner(str(PROJECT_ID), str(SECRET_ID))
        ),
    )

    async def live(session: Any, *, project_id: uuid.UUID) -> list[Any]:
        return [unreadable]

    monkeypatch.setattr(queries, "live_webhook_secrets", live)

    with pytest.raises(UnauthenticatedError):
        await _receive(recorded)


async def test_a_sealed_secret_copied_to_another_row_does_not_open(
    recorded: Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seal names its project and row, so a value moved elsewhere signs nothing."""
    moved = _sealed(LIVE_SECRET)
    moved.id = uuid.uuid4()

    async def live(session: Any, *, project_id: uuid.UUID) -> list[Any]:
        return [moved]

    monkeypatch.setattr(queries, "live_webhook_secrets", live)

    with pytest.raises(UnauthenticatedError):
        await _receive(recorded)


async def test_a_signed_bad_item_is_refused_and_its_refusal_is_kept(recorded: Recorded) -> None:
    """AC9: nothing is stored, the item and field are named, and the refusal is committed
    before the 422 unwinds the request, because the sender proved the secret (D22)."""
    body = _body(_item(), _item(delivery_id="two", url="http://example.com"))

    with pytest.raises(InvalidRequestError) as refused:
        await _receive(recorded, body=body)

    assert refused.value.detail.startswith("Item 2, field `url`")
    assert refused.value.extensions == {"position": 2, "field": "url"}
    assert recorded.refusals == [(2, "url")]
    assert recorded.commits == 1
    assert recorded.used == [SECRET_ID]
    assert recorded.charged == []


async def test_a_dry_run_is_checked_answered_and_stores_nothing(recorded: Recorded) -> None:
    """AC13: last check received moves, nothing is stored or charged (FR11)."""
    answer = await _receive(recorded, body=_body(_item(), dry_run=True))

    assert answer.dry_run is True
    assert answer.stored == 0
    assert answer.message == service.DRY_RUN_MESSAGE
    assert [(one.deploy_id, one.release_id) for one in answer.deploys] == [(None, None)]
    assert recorded.checks == 1
    assert recorded.charged == []


async def test_a_dry_run_with_a_bad_item_is_refused_like_any_other(recorded: Recorded) -> None:
    """Checked in every way: a dry run that passes says the real one would too."""
    with pytest.raises(InvalidRequestError):
        await _receive(recorded, body=_body(_item(commit_sha="nothex"), dry_run=True))

    assert recorded.checks == 0
    assert recorded.refusals == [(1, "commit_sha")]


def test_without_the_credential_key_nothing_is_minted_or_verified() -> None:
    """Refused rather than kept in clear text (design 2)."""
    with pytest.raises(DependencyUnavailableError):
        service.secret_box(ConfiguredKeys(""))


def test_a_token_shaped_metadata_value_is_masked_before_it_is_stored() -> None:
    """AC53, at the row the service builds: the port of the worker's default rules
    (`test_platform_masking.py` holds it to the shared fixture)."""
    item = service._parse(
        _body(_item(metadata={"github_token": "ghp_abc", "branch": "main"})), now=NOW
    ).deploys[0]

    row = service._row(ORG_ID, PROJECT_ID, SECRET_ID, item)

    assert row["event_metadata"] == {"github_token": PLACEHOLDER, "branch": "main"}
    assert row["webhook_secret_id"] == SECRET_ID


# -------------------------------------------------------------------- the routes


def _settings() -> Settings:
    # The credential key, so a deploy secret could be opened: what is refused here is
    # refused for its own reason, not for a missing key.
    return Settings(**TEST_ENVIRONMENT, **DEPLOYED_KEYS)  # type: ignore[arg-type]


def _identity(role: str) -> Identity:
    organization = Organization(slug="acme", name="Acme")
    organization.id = ORG_ID
    user = User(org_id=ORG_ID, email=f"{role}@acme.test", role=role, name=None)
    user.id = uuid.uuid4()
    return Identity(user=user, organization=organization)


def _app(identity: Identity | None = None) -> Any:
    from nexovue_api.main import create_app

    settings = _settings()
    app = create_app(settings)

    async def no_database() -> AsyncIterator[Any]:
        yield object()

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = no_database
    app.dependency_overrides[router._rate_limiter] = lambda: None
    if identity is not None:
        app.dependency_overrides[current_identity_dependency] = lambda: identity
    return app


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.parametrize("role", ["member", "viewer"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/deploys/webhook-secrets?project_id={PROJECT_ID}"),
        ("POST", f"/deploys/webhook-secrets?project_id={PROJECT_ID}"),
        ("DELETE", f"/deploys/webhook-secrets/{SECRET_ID}"),
        ("DELETE", f"/deploys/webhook-secrets/{SECRET_ID}?remove_since=2026-11-08T09:00:00Z"),
        ("DELETE", f"/deploys/{DEPLOY_ID}"),
    ],
)
async def test_a_member_or_viewer_is_refused_every_deploy_setting(
    role: str, method: str, path: str
) -> None:
    """AC2's API half: the section is hidden from them, and the routes refuse them, before
    anything is read."""
    async with _client(_app(_identity(role))) as client:
        response = await client.request(method, f"{API_PREFIX}{path}")

    assert response.status_code == 403


async def test_a_body_one_byte_over_the_cap_is_refused_with_the_stated_words() -> None:
    """AC8's second half, refused before any signature is looked at."""
    body = b"x" * (router.MAX_BODY_BYTES + 1)

    async with _client(_app()) as client:
        response = await client.post(
            f"{API_PREFIX}/deploys/webhook/{PROJECT_ID}",
            content=body,
            headers={signatures.SIGNATURE_HEADER: sign(body, LIVE_SECRET)},
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"] == "A deploy request must be at most 512 KiB; send fewer items."
    )


# ------------------------------------------------- the deploy before something


def _deployed(version: str, service_name: str, at: datetime) -> DeployEventSummary:
    return DeployEventSummary(
        id=uuid.uuid4(),
        project_id=PROJECT_ID,
        version=version,
        service_name=service_name,
        deployed_at=at,
        source="webhook",
        metadata={},
    )


NOON: Final = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

CHECKOUT_OLD: Final = _deployed("1.0.0", "checkout", NOON - timedelta(hours=3))
CHECKOUT_NEW: Final = _deployed("1.1.0", "checkout", NOON - timedelta(minutes=4))
PAYMENTS: Final = _deployed("7.2.1", "payments", NOON - timedelta(minutes=1))
AFTERWARDS: Final = _deployed("1.2.0", "checkout", NOON + timedelta(minutes=30))

HISTORY: Final = service.DeployHistory([CHECKOUT_OLD, CHECKOUT_NEW, PAYMENTS, AFTERWARDS])


def test_the_deploy_before_an_instant_is_the_most_recent_one_of_that_service() -> None:
    found = HISTORY.preceding(services=["checkout"], at=NOON)

    assert found is not None
    assert found.version == CHECKOUT_NEW.version


def test_a_deploy_after_the_instant_never_precedes_it() -> None:
    """It shipped later. Naming it would invert the only thing this claims."""
    found = HISTORY.preceding(services=["checkout"], at=CHECKOUT_NEW.deployed_at)

    assert found is not None
    assert found.version == CHECKOUT_NEW.version

    nothing_yet = HISTORY.preceding(services=["checkout"], at=NOON - timedelta(hours=4))
    assert nothing_yet is None


def test_a_deploy_exactly_at_the_instant_counts_as_preceding_it() -> None:
    """At or before: a deploy and the first error of the build it shipped can
    share a second, and calling that "no deploy" loses the likeliest case."""
    found = HISTORY.preceding(services=["payments"], at=PAYMENTS.deployed_at)

    assert found is not None
    assert found.version == PAYMENTS.version


def test_another_services_deploy_is_never_the_deploy_before_this_one() -> None:
    """`payments` is more recent, and shipped none of `checkout`'s code."""
    found = HISTORY.preceding(services=["checkout"], at=NOON)

    assert found is not None
    assert found.service_name == "checkout"


def test_a_group_spanning_services_takes_the_most_recent_of_any_of_them() -> None:
    found = HISTORY.preceding(services=["checkout", "payments"], at=NOON)

    assert found is not None
    assert found.version == PAYMENTS.version


def test_something_naming_no_service_has_no_deploy_before_it() -> None:
    assert HISTORY.preceding(services=[], at=NOON) is None


def test_a_project_with_no_recorded_deploys_answers_nothing() -> None:
    """Most projects never send a deploy notification, and that is not a failure."""
    assert service.EMPTY_DEPLOY_HISTORY.preceding(services=["checkout"], at=NOON) is None


async def test_both_history_statements_bind_the_organization(
    session: CapturingSession,
) -> None:
    """Either one scoped by project alone would read another tenant's deploys."""
    await service.history_for_window(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        from_time=NOON - timedelta(hours=1),
        to_time=NOON,
    )

    bound = [set(statement.compile().params.values()) for statement in session.statements]
    assert len(bound) == 2
    assert all(ORG_ID in values and PROJECT_ID in values for values in bound)
    assert all(OTHER_ORG_ID not in values for values in bound)


def test_a_window_that_overflows_the_cap_answers_nothing_rather_than_the_wrong_deploy() -> None:
    """Beyond the cap the oldest of the window's deploys were never read, so the
    last deploy before an early instant is one this cannot see. Naming the
    pre-window deploy instead would name one that is provably not the last."""
    horizon = NOON - timedelta(minutes=30)
    capped = service.DeployHistory([CHECKOUT_NEW, PAYMENTS], known_from=horizon)

    assert capped.preceding(services=["checkout"], at=horizon - timedelta(minutes=1)) is None

    still_answered = capped.preceding(services=["checkout"], at=NOON)
    assert still_answered is not None
    assert still_answered.version == CHECKOUT_NEW.version


async def test_the_windows_own_deploys_are_read_under_a_cap(
    session: CapturingSession,
) -> None:
    """`deploy_events` is written by a customer's CI, so how many a window holds
    is their decision and not ours. Every request is bounded."""
    await service.history_for_window(
        session,  # type: ignore[arg-type]
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        from_time=NOON - timedelta(hours=1),
        to_time=NOON,
    )

    inside = session.statements[0].compile()
    assert service.MAX_CORRELATED_DEPLOYS in inside.params.values()
