"""A page served on 127.0.0.1 that no check must ever be able to read.

Stands in for the services that share the browser worker's network. Every hit
is recorded, because "the check failed" is not enough: the assertion that
matters is that the service was never contacted at all.
"""

from __future__ import annotations

import http.server
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

LOOPBACK_ADDRESS: Final = "127.0.0.1"


@dataclass
class LoopbackSite:
    port: int
    secret: str
    hits: list[str] = field(default_factory=list)

    def url(self, host: str = LOOPBACK_ADDRESS) -> str:
        return f"http://{host}:{self.port}/"


@contextmanager
def serve_loopback_site() -> Iterator[LoopbackSite]:
    secret = f"secret-{uuid.uuid4()}"
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # the stdlib's name
            hits.append(self.path)
            body = secret.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer((LOOPBACK_ADDRESS, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield LoopbackSite(port=server.server_address[1], secret=secret, hits=hits)
    finally:
        server.shutdown()
        server.server_close()
