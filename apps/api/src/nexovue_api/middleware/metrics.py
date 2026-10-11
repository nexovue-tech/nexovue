"""The API's own requests, for a monitor to read at `/metrics` (ADR-021).

Labelled by route group — the first segment of the *matched route's template*
— and status class, never by path: a path carries project, trace
and token ids, and a label taken from one lets any caller grow the series count
without bound. A request no route matched is `other`, whatever its path says.

Only the product API is counted; the probes and `/metrics` itself are not.
"""

from __future__ import annotations

import time
from typing import Final

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    disable_created_metrics,
)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexovue_api.settings import API_PREFIX

# A `_created` timestamp beside every counter is noise to every monitor we read with.
disable_created_metrics()  # type: ignore[no-untyped-call]

OTHER_GROUP: Final = "other"
_PREFIX: Final = f"{API_PREFIX}/"

# The console's reads are tens of milliseconds and its slowest searches seconds.
_BUCKETS: Final = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


class _Instruments:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.requests = Counter(
            "nexovue_api_requests",
            "API requests answered, by route group and status class.",
            ["group", "status"],
            registry=registry,
        )
        self.seconds = Histogram(
            "nexovue_api_request_seconds",
            "How long API requests took, by route group.",
            ["group"],
            buckets=_BUCKETS,
            registry=registry,
        )


# Once per process: an app built twice (every test client) must not register the
# same series twice, which the registry refuses.
_PROCESS: _Instruments | None = None


def _process_instruments() -> _Instruments:
    global _PROCESS
    if _PROCESS is None:
        _PROCESS = _Instruments(REGISTRY)
    return _PROCESS


class RequestMetricsMiddleware:
    def __init__(self, app: ASGIApp, *, registry: CollectorRegistry | None = None) -> None:
        self._app = app
        self._instruments = _Instruments(registry) if registry else _process_instruments()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(_PREFIX):
            await self._app(scope, receive, send)
            return
        status = 500
        started = time.perf_counter()

        async def tracked_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, tracked_send)
        finally:
            group = _group(scope)
            self._instruments.requests.labels(group=group, status=f"{status // 100}xx").inc()
            self._instruments.seconds.labels(group=group).observe(time.perf_counter() - started)


def _group(scope: Scope) -> str:
    """The first segment of the matched route's template, which routing leaves in
    the scope. Relative to the innermost router that matched (`/projects`, not
    `/api/v1/projects`) when routers are nested, as they are in `main.py`."""
    template = str(getattr(scope.get("route"), "path", ""))
    template = template.removeprefix(API_PREFIX)
    segment = template.lstrip("/").split("/", 1)[0]
    return segment if segment and not segment.startswith("{") else OTHER_GROUP
