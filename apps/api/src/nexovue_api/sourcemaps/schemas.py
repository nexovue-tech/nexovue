"""What the source map endpoints put on the wire."""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

# A stacktrace longer than this is not one anybody reads, and every frame is a
# lookup against a map held in memory.
MAX_FRAMES: Final = 128


class SourceMapSummary(BaseModel):
    """One uploaded map, as the customer needs to see it.

    `storage_key` is deliberately absent: it names an object in our own store,
    and a client that knows it would be a client building its own URLs into it.
    """

    id: uuid.UUID
    release: str
    # Empty when the bundler emitted no ECMA-426 debug id, never null — the
    # frame then has to be matched on release and path instead.
    debug_id: str
    minified_path: str
    size_bytes: int
    uploaded_at: IsoTimestamp


class SourceMapListResponse(BaseModel):
    source_maps: list[SourceMapSummary] = Field(default_factory=list)


class StackFrame(BaseModel):
    """One frame of a minified browser stacktrace, as the SDK reported it.

    `line` is 1-based and `column` is 1-based, which is what every browser's
    stack and the ECMA-426 mapping both use.
    """

    file: Annotated[str, Field(min_length=1, max_length=2048)]
    line: Annotated[int, Field(ge=1)]
    column: Annotated[int, Field(ge=1)]


class SymbolicateRequest(BaseModel):
    """Which build the frames came from, and the frames themselves.

    One of `debug_id` or `release` identifies the build. The debug id is exact
    when the bundler emitted one; `release` plus each frame's file is the
    fallback, and is why the frames carry their own paths.
    """

    frames: Annotated[list[StackFrame], Field(min_length=1, max_length=MAX_FRAMES)]
    debug_id: Annotated[str, Field(max_length=64)] = ""
    release: Annotated[str, Field(max_length=200)] = ""


class SymbolicatedFrame(BaseModel):
    """One frame after the lookup, or unchanged when no map covered it.

    `resolved` is what a screen branches on. A frame that could not be
    symbolicated keeps its minified position rather than being dropped, because
    a stacktrace missing its middle is worse than one partly minified.
    """

    resolved: bool
    file: str
    line: int
    column: int
    # `<unknown>` whenever the map alone could not name the function. Resolving
    # a name needs the minified bundle beside the map, which is not stored:
    # measured, the file, line, column and source context all resolve without
    # it, and the name is the only thing lost.
    function_name: str = ""
    # The original source around the frame, when the map carried its own
    # `sourcesContent`. Empty when it did not.
    pre_context: list[str] = Field(default_factory=list)
    context_line: str = ""
    post_context: list[str] = Field(default_factory=list)


class SymbolicateResponse(BaseModel):
    frames: list[SymbolicatedFrame] = Field(default_factory=list)
