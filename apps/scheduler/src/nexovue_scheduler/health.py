"""Probe endpoints for a process that has no HTTP surface of its own.

A Celery worker is not a server, but an orchestrator still has to ask the same
two questions, so the answers are served from a small daemon thread alongside
the worker.

``/healthz`` is liveness and touches nothing external — if it checked the broker,
one bad second on Redis would restart every worker at once. ``/readyz`` probes
what this process needs to do work.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

from nexovue_platform.telemetry.logging import get_logger
from nexovue_platform.telemetry.redaction import redact_credentials
from redis import Redis

logger = get_logger(__name__)

LIVENESS_PATH: Final = "/healthz"
READINESS_PATH: Final = "/readyz"

STATUS_OK: Final = "ok"
STATUS_FAILED: Final = "failed"

DEFAULT_PROBE_TIMEOUT_SECONDS: Final = 2.0
MILLISECONDS_PER_SECOND: Final = 1000

DEPENDENCY_REDIS: Final = "redis"

Probe = Callable[[], None]


@dataclass(frozen=True, slots=True)
class Check:
    """One named dependency probe."""

    name: str
    probe: Probe


def redis_check(redis_url: str, *, timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS) -> Check:
    """Probe the Celery broker. The timeout lives on the client, not around it."""
    client = Redis.from_url(
        redis_url,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )

    def probe() -> None:
        client.ping()

    return Check(name=DEPENDENCY_REDIS, probe=probe)


def serve_probes(*, port: int, service: str, checks: list[Check]) -> ThreadingHTTPServer:
    """Start the probe listener on a daemon thread and return the server."""
    handler = _build_handler(service=service, checks=checks)
    server = ThreadingHTTPServer(("", port), handler)
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    logger.info("probe endpoints listening", port=port)
    return server


def _build_handler(*, service: str, checks: list[Check]) -> type[BaseHTTPRequestHandler]:
    class ProbeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == LIVENESS_PATH:
                self._respond(HTTPStatus.OK, {"status": STATUS_OK, "service": service})
                return
            if self.path == READINESS_PATH:
                results = {check.name: _run_check(check) for check in checks}
                ready = all(result["status"] == STATUS_OK for result in results.values())
                status = HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE
                self._respond(
                    status,
                    {
                        "status": STATUS_OK if ready else STATUS_FAILED,
                        "service": service,
                        "checks": results,
                    },
                )
                return
            self._respond(HTTPStatus.NOT_FOUND, {"status": STATUS_FAILED})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — base signature
            """Silence the default stderr access log; we have a structured one."""

        def _respond(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return ProbeHandler


def _run_check(check: Check) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        check.probe()
    except Exception as error:
        # Driver errors quote the DSN often enough that redaction cannot be
        # optional: this body is readable by anything that can reach the port.
        return {
            "status": STATUS_FAILED,
            "duration_ms": _elapsed_ms(started),
            "error": redact_credentials(f"{type(error).__name__}: {error}"),
        }
    return {"status": STATUS_OK, "duration_ms": _elapsed_ms(started)}


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
