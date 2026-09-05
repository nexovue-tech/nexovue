"""Speaking to the egress proxy the way Chromium does, over a raw socket.

A forward proxy sees exactly two request shapes — `CONNECT host:port` for
anything over TLS, and an absolute-form request line for plain HTTP — so a
socket is enough to assert the proxy's own behaviour with no browser in the
way. Shared, because the flow suite has to ask the same questions of the
endpoint the probe handed Chromium.
"""

from __future__ import annotations

import socket
from typing import Final
from urllib.parse import urlsplit

SOCKET_TIMEOUT_SECONDS: Final = 15
HEADER_TERMINATOR: Final = b"\r\n\r\n"
READ_CHUNK_BYTES: Final = 65536

LOOPBACK_ADDRESS: Final = "127.0.0.1"


def open_to(endpoint: str) -> socket.socket:
    """A connection to a `http://host:port` proxy endpoint."""
    parts = urlsplit(endpoint)
    assert parts.hostname is not None
    assert parts.port is not None
    return socket.create_connection((parts.hostname, parts.port), timeout=SOCKET_TIMEOUT_SECONDS)


def connect_request(authority: str) -> bytes:
    return f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode()


def get_request(url: str) -> bytes:
    host = urlsplit(url).netloc
    return f"GET {url} HTTP/1.1\r\nHost: {host}\r\nProxy-Connection: keep-alive\r\n\r\n".encode()


def status_line(response: bytes) -> str:
    return response.split(b"\r\n", 1)[0].decode()


def read_head(sock: socket.socket) -> bytes:
    received = b""
    while HEADER_TERMINATOR not in received:
        chunk = sock.recv(READ_CHUNK_BYTES)
        if not chunk:
            break
        received += chunk
    return received


def read_to_close(sock: socket.socket) -> bytes:
    received = b""
    while chunk := sock.recv(READ_CHUNK_BYTES):
        received += chunk
    return received


def ask(endpoint: str, request: bytes) -> bytes:
    """Send one request, return everything the proxy answered before closing."""
    with open_to(endpoint) as sock:
        sock.sendall(request)
        return read_to_close(sock)


def refuse_loopback_through(endpoint: str, port: int) -> str:
    """The proxy's verdict on a tunnel to loopback, which it must always refuse.

    Used to prove an endpoint is a live boundary rather than a well-formed
    string: nothing else answers `403` to this.
    """
    return status_line(ask(endpoint, connect_request(f"{LOOPBACK_ADDRESS}:{port}")))
