"""Whether this scheduler process is doing its job, for whatever is watching.

`/healthz` is the loop's pulse: a beat that has stopped ticking, or a worker that
has stopped polling, keeps its process and its container up while every
customer's checks or alerts stop (readiness blocker 7). `/readyz` is the loop's
last successful contact with Postgres, the one store it works through.
`/metrics` is the Prometheus text format (ADR-021).

The server runs on its own thread and reads timestamps only, so a hung loop
shows as a stale pulse instead of a probe that hangs with it.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

# Several times the slowest loop period (beat's follower polls every 5 s), so a
# healthy process is never killed by its own healthcheck.
LOOP_SILENCE_LIMIT_SECONDS: Final = 30.0

LIVENESS_PATH: Final = "/healthz"
READINESS_PATH: Final = "/readyz"
METRICS_PATH: Final = "/metrics"


class Pulse:
    """What the loop stamps and the probes read."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # Starting counts as a beat: the loop has not had a chance to tick yet.
        self._beat_at = clock()
        self._postgres_at: float | None = None

    def beat(self) -> None:
        self._beat_at = self._clock()

    def reached_postgres(self) -> None:
        self._postgres_at = self._clock()

    def silent_seconds(self) -> float:
        return self._clock() - self._beat_at

    def postgres_silent_seconds(self) -> float | None:
        return None if self._postgres_at is None else self._clock() - self._postgres_at


def serve(address: str, pulse: Pulse, registry: CollectorRegistry) -> ThreadingHTTPServer:
    """Start answering on `host:port` (`:8081` for every interface) on a daemon thread."""
    host, _, port = address.rpartition(":")
    server = ThreadingHTTPServer((host, int(port)), _handler(pulse, registry))
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    return server


def _handler(pulse: Pulse, registry: CollectorRegistry) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == LIVENESS_PATH:
                silent = pulse.silent_seconds()
                self._json(silent <= LOOP_SILENCE_LIMIT_SECONDS, {"silent_seconds": silent})
            elif self.path == READINESS_PATH:
                since = pulse.postgres_silent_seconds()
                ok = since is not None and since <= LOOP_SILENCE_LIMIT_SECONDS
                self._json(ok, {"postgres_silent_seconds": since})
            elif self.path == METRICS_PATH:
                self._send(HTTPStatus.OK, CONTENT_TYPE_LATEST, generate_latest(registry))
            else:
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"not found\n")

        def _json(self, ok: bool, detail: dict[str, float | None]) -> None:
            body = {"status": "ok" if ok else "failing", **detail}
            status = HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE
            self._send(status, "application/json", json.dumps(body).encode())

        def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Probes every few seconds would drown the log; the loop logs what matters."""

    return Handler
