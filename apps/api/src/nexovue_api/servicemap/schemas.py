"""What the service map endpoint puts on the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

# The two kinds of dependency the pipeline records. A queue hop can be retried
# for hours; a direct call fails the caller now. They are never the same edge.
EDGE_CALL: str = "call"
EDGE_QUEUE: str = "queue"

# Reserved caller and callee names the pipeline writes rather than inventing a
# service. A real `service.name` cannot contain parentheses, so none collides.
#
# `(uninstrumented)` is now the *last* of these, not the only one: a callee that
# never reported for itself is named from what the caller's own span said about
# its peer — `(valkey-cart)`, `(postgresql)`, `(kafka)` — and falls back to
# `(uninstrumented)` only when the span said nothing at all. Every reserved name
# is recognised by its parentheses rather than by this list, which is what stops
# the six unrelated dependencies that once shared one node from sharing one node.
UNINSTRUMENTED_SERVICE: str = "(uninstrumented)"
UNKNOWN_SERVICE: str = "(unknown)"


def is_reserved_service(name: str) -> bool:
    """Whether this name was written by us rather than reported by a service."""
    return name.startswith("(") and name.endswith(")")


class ServiceEdge(BaseModel):
    """One caller→callee dependency over the window."""

    caller: str
    callee: str
    # "call" or "queue".
    edge_kind: str
    # The technology the caller says it reached this callee over — `redis`,
    # `postgresql`, `kafka`, `grpc`. Empty whenever the callee reported for
    # itself: a service that names itself needs no label, and what the caller
    # believed about it is not evidence about the callee.
    callee_kind: str = ""
    call_count: int
    error_count: int
    calls_per_minute: float
    # A fraction, not a percentage: 0.05 is one call in twenty.
    error_rate: float
    # The caller's own span duration, in both kinds of edge. On a `call` that is
    # the request the caller waited on, network included. On a `queue` it is the
    # publish — how long sending the message took — and *not* the delivery lag,
    # which would have to be measured across the producer's clock and the
    # consumer's.
    p50_ms: float
    p95_ms: float
    p99_ms: float


class ServiceMapResponse(BaseModel):
    """The graph: its nodes, and the edges that put them there."""

    window_start: IsoTimestamp
    window_end: IsoTimestamp
    # Every service named by an edge, sorted. Derived from the edges rather than
    # queried, so the graph can never carry an edge to a node it does not list.
    # A service with no dependencies either way is not on the map — it has no
    # edge to put it there — and is found in the service list instead.
    services: list[str] = Field(default_factory=list)
    edges: list[ServiceEdge] = Field(default_factory=list)
