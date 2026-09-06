"""Turning source map rows into the shapes the API promises.

No SQL text and no HTTP. What lives here is where an uploaded map's bytes go,
which stored map answers a stack frame, and what a frame becomes when none does.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from symbolic.sourcemapcache import SourceMapCache

from nexovue_api.data.objectstore import ObjectStore, ObjectStoreError
from nexovue_api.data.postgres.models import SourceMap
from nexovue_api.errors.exceptions import DependencyUnavailableError, InvalidRequestError
from nexovue_api.settings import OBJECT_STORE_VARIABLES
from nexovue_api.sourcemaps import queries, symbolication
from nexovue_api.sourcemaps.schemas import (
    SourceMapListResponse,
    SourceMapSummary,
    StackFrame,
    SymbolicateResponse,
)

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200

# A source map is routinely larger than the bundle it describes. This is the
# largest single one accepted: past it the upload is refused with the limit
# named, rather than being cut short and stored as a map that cannot be parsed.
MAX_MAP_BYTES: Final = 64 * 1024 * 1024

# What a source map is served and stored as. `.map` files are JSON.
MAP_CONTENT_TYPE: Final = "application/json"

# Where a map's bytes live, under the tenant that owns them. The org and project
# are in the key so an object cannot be reached from the wrong tenant's row even
# if a key were ever mixed up, and the row id makes each upload its own object —
# a re-uploaded build supersedes in the table, never by overwriting bytes another
# row still points at.
_KEY_TEMPLATE: Final = "source-maps/{org_id}/{project_id}/{source_map_id}.map"


async def list_source_maps(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int
) -> SourceMapListResponse:
    """The maps uploaded for a project, newest first."""
    rows = await queries.list_for_project(
        session, org_id=org_id, project_id=project_id, limit=limit
    )
    return SourceMapListResponse(source_maps=[_to_summary(row) for row in rows])


async def store_source_map(
    session: AsyncSession,
    store: ObjectStore | None,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    release: str,
    minified_path: str,
    debug_id: str,
    body: bytes,
) -> SourceMapSummary:
    """Put one map's bytes in the object store and record where they went.

    The bytes go first. A row pointing at an object that was never written would
    make every later symbolication of that build fail on a read; an object with
    no row is unreferenced storage, which a sweep can find and this cannot.
    """
    object_store = _require_store(store)
    _refuse_unusable(body)

    source_map_id = uuid.uuid4()
    key = _KEY_TEMPLATE.format(org_id=org_id, project_id=project_id, source_map_id=source_map_id)
    try:
        stored = await object_store.put(key, body, content_type=MAP_CONTENT_TYPE)
    except ObjectStoreError as error:
        raise DependencyUnavailableError("The object store did not accept the upload.") from error

    row = await queries.insert(
        session,
        SourceMap(
            id=source_map_id,
            org_id=org_id,
            project_id=project_id,
            release=release,
            debug_id=debug_id or None,
            minified_path=minified_path,
            storage_key=stored.key,
            size_bytes=stored.size_bytes,
        ),
    )
    return _to_summary(row)


async def symbolicate(
    session: AsyncSession,
    store: ObjectStore | None,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    frames: list[StackFrame],
    debug_id: str,
    release: str,
) -> SymbolicateResponse:
    """Resolve every frame against the map that covers it.

    Frames are grouped by file so one map is fetched and parsed once however
    many frames it answers — a stacktrace is usually many frames of two or three
    bundles, and parsing a map is the expensive half.
    """
    if not debug_id and not release:
        raise InvalidRequestError("Symbolication needs either a debug_id or a release.")
    object_store = _require_store(store)

    # A file absent from the map is a miss already looked up, not one still to
    # do, so a stacktrace of forty frames in one uncovered bundle costs one
    # fetch rather than forty.
    caches: dict[str, SourceMapCache | None] = {}
    resolved = []
    for frame in frames:
        if frame.file not in caches:
            caches[frame.file] = await _cache_for(
                session,
                object_store,
                org_id=org_id,
                project_id=project_id,
                debug_id=debug_id,
                release=release,
                minified_path=frame.file,
            )
        cache = caches[frame.file]
        if cache is None:
            resolved.append(symbolication.unresolved(frame))
        else:
            resolved.append(symbolication.resolve(cache, frame))
    return SymbolicateResponse(frames=resolved)


async def _cache_for(
    session: AsyncSession,
    store: ObjectStore,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    debug_id: str,
    release: str,
    minified_path: str,
) -> SourceMapCache | None:
    """The parsed map for one minified file, or None when nothing covers it.

    The debug id is tried first because it is exact. Falling back to release and
    path is what makes the common bundler — one that emits no debug id — work at
    all.
    """
    row = None
    if debug_id:
        row = await queries.find_by_debug_id(
            session, org_id=org_id, project_id=project_id, debug_id=debug_id
        )
    if row is None and release:
        row = await queries.find_by_release_and_path(
            session,
            org_id=org_id,
            project_id=project_id,
            release=release,
            minified_path=minified_path,
        )
    if row is None:
        return None

    try:
        body = await store.get(row.storage_key)
    except ObjectStoreError as error:
        raise DependencyUnavailableError("The object store did not return a source map.") from error
    try:
        return symbolication.load(body)
    except symbolication.SourceMapUnreadableError:
        # A stored map we cannot parse is our problem, not the caller's, and it
        # must not fail the whole trace: the other frames still resolve.
        return None


def _require_store(store: ObjectStore | None) -> ObjectStore:
    """The object store, or a failure that names what has to be configured."""
    if store is None:
        raise DependencyUnavailableError(
            "Source maps need an object store. Set " + ", ".join(OBJECT_STORE_VARIABLES) + "."
        )
    return store


def _refuse_unusable(body: bytes) -> None:
    """Refuse an upload that could never symbolicate anything."""
    if not body:
        raise InvalidRequestError("The uploaded source map is empty.")
    if len(body) > MAX_MAP_BYTES:
        raise InvalidRequestError(f"The source map is larger than the {MAX_MAP_BYTES} byte limit.")


def _to_summary(row: SourceMap) -> SourceMapSummary:
    return SourceMapSummary(
        id=row.id,
        release=row.release,
        debug_id=row.debug_id or "",
        minified_path=row.minified_path,
        size_bytes=row.size_bytes,
        uploaded_at=row.uploaded_at,
    )
