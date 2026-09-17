"""A small HTTP service that records exactly what each request carried.

Live probe tests send real requests through pycurl to this and assert on what
arrived — method, path, headers, body — because "the check passed" says nothing
about whether the right bytes were sent (AC1, AC2).

It listens on this machine's **private** address rather than on loopback. The
connection guard refuses loopback at every setting, by design, so a test that
needed loopback would need the guard weakened; a private address is reachable
with the development escape the prober already has.
"""

from __future__ import annotations

import http.server
import json
import socket
import socketserver
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

# Any routable address works: no packet is sent, the kernel only picks the
# interface it would use.
_ROUTE_PROBE: Final = ("10.255.255.255", 1)


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Reply:
    status: int = 200
    # Seconds to wait before answering, for timing thresholds.
    delay: float = 0.0
    body: bytes = b'{"ok": true}'
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})


@dataclass
class RecordingSite:
    host: str
    port: int
    requests: list[RecordedRequest]
    # Path to the reply it gets; unknown paths get the default.
    replies: dict[str, Reply | Callable[[RecordedRequest], Reply]]

    def url(self, path: str = "/") -> str:
        return f"http://{self.host}:{self.port}{path}"


def private_address() -> str:
    """This machine's private address, or a clear failure when it has none."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(_ROUTE_PROBE)
        address = str(probe.getsockname()[0])
    finally:
        probe.close()
    if address.startswith("127."):
        raise RuntimeError(
            "the live probe suites need a private network address, and this machine has none"
        )
    return address


@contextmanager
def serve_recording_site() -> Iterator[RecordingSite]:
    requests: list[RecordedRequest] = []
    replies: dict[str, Reply | Callable[[RecordedRequest], Reply]] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            recorded = RecordedRequest(
                method=self.command,
                path=self.path,
                headers={name.lower(): value for name, value in self.headers.items()},
                body=body,
            )
            requests.append(recorded)
            configured = replies.get(self.path.split("?", 1)[0], Reply())
            reply = configured(recorded) if callable(configured) else configured
            if reply.delay:
                time.sleep(reply.delay)
            self.send_response(reply.status)
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(reply.body)

        def __getattr__(self, name: str) -> Callable[[], None]:
            # Every method — GET as much as PROPFIND — is one handler: the stdlib
            # looks up `do_<METHOD>`, and each lookup lands here.
            if name.startswith("do_"):
                return self._handle
            raise AttributeError(name)

        def log_message(self, *_: object) -> None:
            pass

    class Server(http.server.ThreadingHTTPServer):
        def server_bind(self) -> None:
            # The stdlib's own bind does a reverse DNS lookup of the address,
            # which stalls five seconds on a machine with no answer for it.
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "recording-site", self.server_address[1]

        def handle_error(self, request: object, client_address: object) -> None:
            # A probe that stops reading at its body cap hangs up mid-reply, on purpose.
            pass

    server = Server(("0.0.0.0", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield RecordingSite(
            host=private_address(),
            port=server.server_address[1],
            requests=requests,
            replies=replies,
        )
    finally:
        server.shutdown()
        server.server_close()


def json_reply(document: object, status: int = 200) -> Reply:
    return Reply(status=status, body=json.dumps(document).encode())
