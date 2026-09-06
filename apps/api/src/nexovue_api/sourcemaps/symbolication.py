"""Turning a minified stack frame back into the source the developer wrote.

The lookup itself is Sentry's `symbolic`. What lives here is the part that is
ours: which map answers a frame, and what a frame becomes when none does.

**The minified bundle is not stored, and does not need to be.** `symbolic`'s
`SourceMapCache.from_bytes` takes the minified source beside the map, and the
difference it makes was measured rather than assumed: with the bundle a frame
resolves to `Checkout.tsx:1` and the function name `greet`; with empty bytes in
its place the same frame resolves to `Checkout.tsx:1` with the function name
`<unknown>`, and the surrounding source lines are identical either way — they
come from the map's own `sourcesContent`. So the bundle buys one field, and
would cost storing and retaining a second artifact the size of the customer's
whole application for every build.
"""

from __future__ import annotations

from typing import Final

from symbolic.sourcemapcache import SourceMapCache

from nexovue_api.sourcemaps.schemas import StackFrame, SymbolicatedFrame

# How many original lines to return either side of the frame. Enough to read the
# statement in context, few enough that a 128-frame trace stays a small response.
CONTEXT_LINES: Final = 5

# What `symbolic` returns when the map cannot name the function, which is always
# for us: naming it needs the minified bundle. Recognised so it is reported as
# "not named" rather than as a function actually called `<unknown>`.
UNKNOWN_FUNCTION: Final = "<unknown>"

# Stands in for the minified source we do not store. `from_bytes` requires the
# argument; empty is what makes the function name unavailable and nothing else.
_NO_MINIFIED_SOURCE: Final = b""


class SourceMapUnreadableError(Exception):
    """The stored bytes are not a source map `symbolic` can parse."""


def load(map_bytes: bytes) -> SourceMapCache:
    """Parse one stored map, ready to answer frames."""
    try:
        return SourceMapCache.from_bytes(_NO_MINIFIED_SOURCE, map_bytes)
    except Exception as error:  # symbolic raises its own Rust-backed types
        raise SourceMapUnreadableError("the stored source map could not be parsed") from error


def resolve(cache: SourceMapCache, frame: StackFrame) -> SymbolicatedFrame:
    """Look one frame up, or return it unchanged when the map does not cover it.

    A frame outside the map is kept rather than dropped: a stacktrace missing
    its middle is harder to read than one that is partly still minified.
    """
    token = cache.lookup(frame.line, frame.column, CONTEXT_LINES)
    if token is None:
        return unresolved(frame)

    name = token.function_name or ""
    return SymbolicatedFrame(
        resolved=True,
        file=token.src or frame.file,
        line=token.line,
        column=token.col,
        function_name="" if name == UNKNOWN_FUNCTION else name,
        pre_context=list(token.pre_context or []),
        context_line=token.context_line or "",
        post_context=list(token.post_context or []),
    )


def unresolved(frame: StackFrame) -> SymbolicatedFrame:
    """The frame as it arrived, marked as not symbolicated."""
    return SymbolicatedFrame(resolved=False, file=frame.file, line=frame.line, column=frame.column)
