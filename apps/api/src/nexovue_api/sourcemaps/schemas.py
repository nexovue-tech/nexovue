"""What the source map endpoints put on the wire."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp


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
