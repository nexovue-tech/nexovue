"""What the service map endpoint puts on the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexovue_api.wire import IsoTimestamp

# The two kinds of dependency the pipeline records. A queue hop can be retried
# for hours; a direct call fails the caller now. They are never the same edge.
EDGE_CALL: str = "call"
EDGE_QUEUE: str = "queue"

# Reserved callee and caller names the pipeline writes rather than inventing a
# service. A real `service.name` cannot contain parentheses, so neither collides.
UNINSTRUMENTED_SERVICE: str = "(uninstrumented)"
UNKNOWN_SERVICE: str = "(unknown)"


class ServiceEdge(BaseModel):
    """One caller→callee dependency over the window."""

    caller: str
    callee: str
    # "call" or "queue".
    edge_kind: str
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
