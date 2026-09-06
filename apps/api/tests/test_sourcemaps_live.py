"""Source map upload and symbolication against real SeaweedFS and Postgres.

A unit test with a fake store proves the code calls a method. It cannot prove
that SeaweedFS accepts the key we build, that the bytes come back identical, or
that `symbolic` resolves a real `.map` — and those are the three things that
turn a minified stacktrace into something a developer can read.

Everything Postgres touches runs inside one transaction that is rolled back on
the way out. The objects written to the store are deleted explicitly, because
an object store has no transaction to roll back.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import pytest
from live import require_stack
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from nexovue_api.data.objectstore import ObjectStore
from nexovue_api.errors.exceptions import DependencyUnavailableError, InvalidRequestError
from nexovue_api.settings import ObjectStoreSettings
from nexovue_api.signup.service import provision_tenant
from nexovue_api.sourcemaps import service
from nexovue_api.sourcemaps.schemas import StackFrame

POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)
ASYNC_DSN: Final = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)

S3_ENDPOINT: Final = os.environ.get("NEXOVUE_TEST_S3_ENDPOINT", "http://localhost:18333")
S3_BUCKET: Final = "nexovue-test-sourcemaps"

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0

# A real, minimal source map: the minified `a.js` came from `Checkout.tsx`, and
# `mappings` puts column 9 of its only line at the `greet` declaration.
MINIFIED_PATH: Final = "static/js/a.3f9c.js"
SOURCE_MAP: Final = json.dumps(
    {
        "version": 3,
        "file": "a.3f9c.js",
        "sources": ["Checkout.tsx"],
        "sourcesContent": ["function greet() {\n  throw new Error('checkout failed')\n}\n"],
        "names": ["greet"],
        "mappings": "AAAA,SAASA",
    }
).encode()

RELEASE: Final = "2026.09.06-1"
DEBUG_ID: Final = "b3f1c2d4e5f60718293a4b5c6d7e8f90"


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", parts.port or default_port),
            REACHABILITY_TIMEOUT_SECONDS,
        ):
            return True
    except OSError:
        return False


require_stack(
    reachable=_reachable(POSTGRES_DSN, 5432) and _reachable(S3_ENDPOINT, 8333),
    suite="the live source map suite",
    remedy="Postgres or SeaweedFS is not running — start them with `just dev`",
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session in a transaction that is always rolled back."""
    engine = create_async_engine(ASYNC_DSN)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def store() -> AsyncIterator[ObjectStore]:
    """A real client against a bucket this suite owns and empties."""
    written: list[str] = []
    store = ObjectStore(
        ObjectStoreSettings(
            endpoint=S3_ENDPOINT,
            bucket=S3_BUCKET,
            access_key_id=os.environ.get("NEXOVUE_TEST_S3_KEY", "nexovue_dev_access"),
            secret_access_key=os.environ.get("NEXOVUE_TEST_S3_SECRET", "nexovue_dev_secret"),
            region="us-east-1",
        )
    )
    await store.ensure_bucket()

    # Remember every key the suite writes, so the store is left as it was found.
    original_put = store.put

    async def remembering_put(key: str, body: bytes, *, content_type: str):  # type: ignore[no-untyped-def]
        written.append(key)
        return await original_put(key, body, content_type=content_type)

    store.put = remembering_put  # type: ignore[method-assign]
    try:
        yield store
    finally:
        for key in written:
            await store.delete(key)


async def _tenant(db: AsyncSession, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    """One organization and one project, made the way a real signup makes them."""
    provisioned = await provision_tenant(
        db,
        organization_name="Probe",
        email=email,
        password="correct horse battery staple",
    )
    await db.flush()
    return provisioned.organization.id, provisioned.project.id


async def test_an_uploaded_map_turns_a_minified_frame_back_into_source(
    db: AsyncSession, store: ObjectStore
) -> None:
    org_id, project_id = await _tenant(db, f"maps-{uuid.uuid4()}@example.test")

    uploaded = await service.store_source_map(
        db,
        store,
        org_id=org_id,
        project_id=project_id,
        release=RELEASE,
        minified_path=MINIFIED_PATH,
        debug_id=DEBUG_ID,
        body=SOURCE_MAP,
    )
    assert uploaded.size_bytes == len(SOURCE_MAP)
    assert uploaded.debug_id == DEBUG_ID

    answer = await service.symbolicate(
        db,
        store,
        org_id=org_id,
        project_id=project_id,
        frames=[StackFrame(file=MINIFIED_PATH, line=1, column=9)],
        debug_id=DEBUG_ID,
        release="",
    )

    frame = answer.frames[0]
    assert frame.resolved, "the frame did not symbolicate against its own map"
    assert frame.file == "Checkout.tsx"
    assert frame.line == 1
    # The map carried sourcesContent, so the developer sees the line they wrote.
    assert frame.context_line.strip() == "function greet() {"
    # No minified bundle is stored, so the function name is the one thing lost.
    assert frame.function_name == ""


async def test_a_build_with_no_debug_id_is_found_by_release_and_path(
    db: AsyncSession, store: ObjectStore
) -> None:
    org_id, project_id = await _tenant(db, f"release-{uuid.uuid4()}@example.test")

    await service.store_source_map(
        db,
        store,
        org_id=org_id,
        project_id=project_id,
        release=RELEASE,
        minified_path=MINIFIED_PATH,
        debug_id="",
        body=SOURCE_MAP,
    )

    answer = await service.symbolicate(
        db,
        store,
        org_id=org_id,
        project_id=project_id,
        frames=[StackFrame(file=MINIFIED_PATH, line=1, column=9)],
        debug_id="",
        release=RELEASE,
    )
    assert answer.frames[0].resolved, "the release fallback did not find the map"
    assert answer.frames[0].file == "Checkout.tsx"


async def test_another_organizations_map_is_not_reachable(
    db: AsyncSession, store: ObjectStore
) -> None:
    """Tenant isolation, which is a Phase 1 invariant, at the repository layer."""
    owner_org, owner_project = await _tenant(db, f"owner-{uuid.uuid4()}@example.test")
    other_org, other_project = await _tenant(db, f"other-{uuid.uuid4()}@example.test")

    await service.store_source_map(
        db,
        store,
        org_id=owner_org,
        project_id=owner_project,
        release=RELEASE,
        minified_path=MINIFIED_PATH,
        debug_id=DEBUG_ID,
        body=SOURCE_MAP,
    )

    answer = await service.symbolicate(
        db,
        store,
        org_id=other_org,
        project_id=other_project,
        frames=[StackFrame(file=MINIFIED_PATH, line=1, column=9)],
        debug_id=DEBUG_ID,
        release=RELEASE,
    )
    frame = answer.frames[0]
    assert not frame.resolved, "one organization symbolicated against another's source map"
    # The frame comes back as it went in rather than being dropped.
    assert frame.file == MINIFIED_PATH and frame.line == 1


async def test_a_frame_no_map_covers_survives_the_lookup(
    db: AsyncSession, store: ObjectStore
) -> None:
    org_id, project_id = await _tenant(db, f"uncovered-{uuid.uuid4()}@example.test")

    answer = await service.symbolicate(
        db,
        store,
        org_id=org_id,
        project_id=project_id,
        frames=[
            StackFrame(file="static/js/vendor.js", line=2, column=15),
            StackFrame(file="static/js/vendor.js", line=9, column=4),
        ],
        debug_id="",
        release=RELEASE,
    )
    assert [frame.resolved for frame in answer.frames] == [False, False]
    assert [frame.line for frame in answer.frames] == [2, 9]


async def test_an_empty_upload_is_refused(db: AsyncSession, store: ObjectStore) -> None:
    org_id, project_id = await _tenant(db, f"empty-{uuid.uuid4()}@example.test")

    with pytest.raises(InvalidRequestError):
        await service.store_source_map(
            db,
            store,
            org_id=org_id,
            project_id=project_id,
            release=RELEASE,
            minified_path=MINIFIED_PATH,
            debug_id="",
            body=b"",
        )


async def test_without_an_object_store_the_failure_names_the_variables(
    db: AsyncSession,
) -> None:
    org_id, project_id = await _tenant(db, f"nostore-{uuid.uuid4()}@example.test")

    with pytest.raises(DependencyUnavailableError) as failure:
        await service.store_source_map(
            db,
            None,
            org_id=org_id,
            project_id=project_id,
            release=RELEASE,
            minified_path=MINIFIED_PATH,
            debug_id="",
            body=SOURCE_MAP,
        )
    assert "S3_ENDPOINT" in str(failure.value)
