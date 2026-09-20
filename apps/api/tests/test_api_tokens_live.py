"""API tokens (F36, AC13, AC16, AC17) against real Postgres. Rolled back per test.

What it proves: a token is shown once and stored only as a hash; it reads only
for the organization that owns it and only within its scope; revoked and expired
tokens are refused on the next request, each with its own sentence; a token
outlives the person who made it; and every mint and withdrawal is audited without
the secret.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.apitokens import service
from nexovue_api.apitokens.tokens import API_TOKEN_PREFIX, hash_api_token
from nexovue_api.data.postgres.models import ApiToken, ApiTokenUsageDaily
from nexovue_api.errors.exceptions import NotFoundError, UnauthenticatedError
from nexovue_api.signup.service import ProvisionedTenant, provision_tenant

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
PASSWORD: Final = "correct horse battery staple"
NINETY_DAYS: Final = 90


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 5432), 2):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN),
    suite="apps/api/tests/test_api_tokens_live.py",
    remedy="Postgres is not reachable — run `just up`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


async def _tenant(db: AsyncSession, name: str = "Acme") -> ProvisionedTenant:
    return await provision_tenant(
        db,
        organization_name=name,
        email=f"{name.lower()}-{uuid.uuid4()}@example.test",
        password=PASSWORD,
    )


async def _mint(
    db: AsyncSession,
    tenant: ProvisionedTenant,
    *,
    name: str = "CI",
    project_id: uuid.UUID | None = None,
    days: int = NINETY_DAYS,
) -> tuple[str, uuid.UUID]:
    made = await service.create(
        db,
        org_id=tenant.organization.id,
        actor=tenant.user,
        name=name,
        project_id=project_id,
        expires_in_days=days,
        now=_now(),
    )
    return made.token.get_secret_value(), made.api_token.id


async def _audit(db: AsyncSession, org_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = await db.execute(
        text(
            "SELECT action, coalesce(detail::text, '') FROM audit_log WHERE org_id = :org "
            "AND action LIKE 'api_token.%' ORDER BY at, id"
        ),
        {"org": org_id},
    )
    return [(action, detail) for action, detail in rows.tuples()]


async def test_a_token_is_shown_once_and_kept_only_as_a_hash(db: AsyncSession) -> None:
    """AC13, BR10."""
    acme = await _tenant(db)

    made = await service.create(
        db,
        org_id=acme.organization.id,
        actor=acme.user,
        name="CI",
        project_id=acme.project.id,
        expires_in_days=NINETY_DAYS,
        now=_now(),
    )
    plaintext = made.token.get_secret_value()

    assert plaintext.startswith(API_TOKEN_PREFIX)
    stored = await db.get(ApiToken, made.api_token.id)
    assert stored is not None
    assert stored.token_hash == hash_api_token(plaintext)
    # Nothing stored is the secret, and the prefix is the only part in the clear.
    assert plaintext not in (stored.token_prefix, stored.token_hash)
    assert plaintext.startswith(stored.token_prefix)
    assert made.api_token.token_hint.endswith(stored.token_prefix[-4:])
    assert made.api_token.status == service.LIVE


async def test_the_token_a_caller_presents_names_its_organization(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    plaintext, token_id = await _mint(db, acme, project_id=acme.project.id)

    found = await service.token_for(db, plaintext, now=_now())

    assert found.id == token_id
    assert found.org_id == acme.organization.id
    assert found.project_id == acme.project.id
    assert found.role == "read"


async def test_an_invented_token_is_refused_without_saying_more(db: AsyncSession) -> None:
    with pytest.raises(UnauthenticatedError) as refused:
        await service.token_for(db, f"{API_TOKEN_PREFIX}notarealtokenatall", now=_now())

    assert refused.value.detail == service.TOKEN_UNKNOWN


async def test_a_revoked_token_is_refused_on_its_very_next_request(db: AsyncSession) -> None:
    """AC16. Revoked and unknown answer alike: neither says whether it was real."""
    acme = await _tenant(db)
    plaintext, token_id = await _mint(db, acme)
    assert await service.token_for(db, plaintext, now=_now())

    await service.revoke(
        db, org_id=acme.organization.id, actor=acme.user, token_id=token_id, now=_now()
    )

    with pytest.raises(UnauthenticatedError) as refused:
        await service.token_for(db, plaintext, now=_now())
    assert refused.value.detail == service.TOKEN_UNKNOWN


async def test_an_expired_token_says_when_it_expired(db: AsyncSession) -> None:
    """AC16: the customer owns that date, so "it stopped working" is not enough."""
    acme = await _tenant(db)
    plaintext, _ = await _mint(db, acme, days=1)

    with pytest.raises(UnauthenticatedError) as refused:
        await service.token_for(db, plaintext, now=_now() + timedelta(days=2))

    assert "expired on" in refused.value.detail
    assert API_TOKEN_PREFIX not in refused.value.detail


async def test_a_token_whose_project_is_deleted_is_refused(db: AsyncSession) -> None:
    acme = await _tenant(db)
    plaintext, _ = await _mint(db, acme, project_id=acme.project.id)

    acme.project.deleted_at = _now()
    await db.flush()

    with pytest.raises(UnauthenticatedError) as refused:
        await service.token_for(db, plaintext, now=_now())
    assert refused.value.detail == service.TOKEN_PROJECT_GONE


async def test_a_token_outlives_the_person_who_made_it(db: AsyncSession) -> None:
    """AC17: CI does not break when whoever set it up leaves."""
    acme = await _tenant(db)
    plaintext, token_id = await _mint(db, acme)

    await db.execute(
        text("UPDATE api_tokens SET created_by = NULL WHERE id = :id"), {"id": token_id}
    )

    still_live = await service.token_for(db, plaintext, now=_now())
    assert still_live.id == token_id
    listed = await service.list_for_organization(db, org_id=acme.organization.id, now=_now())
    assert [token.created_by_name for token in listed.api_tokens] == [None]


async def test_another_organizations_token_is_not_revocable_or_listed(db: AsyncSession) -> None:
    acme = await _tenant(db, "Acme")
    other = await _tenant(db, "Globex")
    _, their_token = await _mint(db, other)

    with pytest.raises(NotFoundError):
        await service.revoke(
            db, org_id=acme.organization.id, actor=acme.user, token_id=their_token, now=_now()
        )

    listed = await service.list_for_organization(db, org_id=acme.organization.id, now=_now())
    assert listed.api_tokens == []


async def test_the_list_shows_what_each_token_is_and_what_it_spent(db: AsyncSession) -> None:
    """AC18's Postgres half: the counter the minute-fold writes is what is shown."""
    acme = await _tenant(db)
    _, token_id = await _mint(db, acme, name="Nightly", project_id=acme.project.id)
    db.add(ApiTokenUsageDaily(token_id=token_id, day=_now().date(), requests=41))
    await db.flush()

    [listed] = (
        await service.list_for_organization(db, org_id=acme.organization.id, now=_now())
    ).api_tokens

    assert listed.name == "Nightly"
    assert listed.project_id == acme.project.id
    assert listed.created_by_name == acme.user.email
    assert listed.requests_today == 41
    assert listed.status == service.LIVE


async def test_a_revoked_token_reads_as_revoked_and_an_old_one_as_expired(
    db: AsyncSession,
) -> None:
    acme = await _tenant(db)
    _, revoked = await _mint(db, acme, name="Old CI")
    _, ending = await _mint(db, acme, name="Short", days=1)

    summary = await service.revoke(
        db, org_id=acme.organization.id, actor=acme.user, token_id=revoked, now=_now()
    )
    later = await service.list_for_organization(
        db, org_id=acme.organization.id, now=_now() + timedelta(days=2)
    )
    by_id = {token.id: token for token in later.api_tokens}

    assert summary.status == service.REVOKED
    assert by_id[revoked].status == service.REVOKED
    assert by_id[ending].status == service.EXPIRED


async def test_minting_and_withdrawing_are_audited_without_the_secret(db: AsyncSession) -> None:
    acme = await _tenant(db)
    plaintext, token_id = await _mint(db, acme, name="CI")

    await service.revoke(
        db, org_id=acme.organization.id, actor=acme.user, token_id=token_id, now=_now()
    )
    await db.flush()

    entries = await _audit(db, acme.organization.id)
    assert [action for action, _ in entries] == ["api_token.created", "api_token.revoked"]
    assert all(plaintext not in detail for _, detail in entries)
    assert "CI" in entries[0][1]


async def test_revoking_twice_changes_nothing_and_records_once(db: AsyncSession) -> None:
    acme = await _tenant(db)
    _, token_id = await _mint(db, acme)

    first = await service.revoke(
        db, org_id=acme.organization.id, actor=acme.user, token_id=token_id, now=_now()
    )
    second = await service.revoke(
        db, org_id=acme.organization.id, actor=acme.user, token_id=token_id, now=_now()
    )
    await db.flush()

    assert first.revoked_at == second.revoked_at
    assert [action for action, _ in await _audit(db, acme.organization.id)] == [
        "api_token.created",
        "api_token.revoked",
    ]
