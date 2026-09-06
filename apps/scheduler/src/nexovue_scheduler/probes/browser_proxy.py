"""The browser's egress boundary: a forward proxy that only reaches public addresses.

An HTTP check is guarded at libcurl's socket factory — see `addresses`. Chromium
has no such hook. It resolves its own names and opens its own sockets, so a
browser flow pointed at `127.0.0.1.nip.io` reached loopback inside the worker
container, and `expect_text` reported back what it found there.

A proxy closes that because **resolving the name and opening the connection are
one step here**. `getaddrinfo` gives every candidate address, each one is judged,
and the socket is connected to the literal address that was judged — so there is
no window between the decision and the connection for the answer to change.
That is the same property `ConnectGuard` has, obtained the only way Chromium
allows.

Chromium is launched with this proxy and nothing else, so a name it cannot reach
through here it cannot reach at all — see `browser.launch_options`.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from dataclasses import dataclass
from types import TracebackType
from typing import Final
from urllib.parse import SplitResult, urlsplit

from nexovue_scheduler.probes.addresses import may_connect_to

# Loopback only. The proxy runs inside the browser worker and is for that
# worker's Chromium; binding it to the container's network address would offer
# every other container a way through our own address check.
LISTEN_HOST: Final = "127.0.0.1"
EPHEMERAL_PORT: Final = 0

METHOD_CONNECT: Final = "CONNECT"
SCHEME_HTTP: Final = "http"
DEFAULT_HTTP_PORT: Final = 80

# What a TCP port can be. Not a policy about *which* ports a flow may reach —
# that is still open — only the range outside which a number is not a port.
MIN_PORT: Final = 1
MAX_PORT: Final = 65535

HEADER_TERMINATOR: Final = b"\r\n\r\n"
LINE_TERMINATOR: Final = b"\r\n"
HEADER_ENCODING: Final = "latin-1"
HEADER_SEPARATOR: Final = b":"

HEADER_CONTENT_LENGTH: Final = "content-length"
HEADER_TRANSFER_ENCODING: Final = "transfer-encoding"

# A request head larger than this is not a browser's. Bounds what a hostile page
# can make the proxy hold before it has decided anything.
MAX_HEAD_BYTES: Final = 64 * 1024
RELAY_CHUNK_BYTES: Final = 64 * 1024

# Headers that belong to one hop and must not be forwarded to the origin.
# `connection` goes because the value below replaces it.
HOP_BY_HOP_HEADERS: Final = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection"}
)

# Asks the origin for one exchange, so the response ends at its EOF and nothing
# has to trust a Content-Length we did not write. What stops a *second request*
# reaching an origin judged for the first is `body_bytes`, not this header.
FORWARDED_CONNECTION_HEADER: Final = b"Connection: close"

CONNECT_TIMEOUT_SECONDS: Final = 10.0
STARTUP_TIMEOUT_SECONDS: Final = 10.0
SHUTDOWN_TIMEOUT_SECONDS: Final = 5.0

THREAD_NAME: Final = "browser-egress-proxy"

TUNNEL_ESTABLISHED: Final = b"HTTP/1.1 200 Connection Established\r\n\r\n"


def _response(status: int, reason: str, body: str = "") -> bytes:
    """A complete one-hop response. The length is computed, never counted by hand."""
    payload = body.encode(HEADER_ENCODING)
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: text/plain\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n\r\n"
    )
    return head.encode(HEADER_ENCODING) + payload


# 403 when we refused the address, 502 when nothing answered. The distinction is
# the point of judging per request: a rebinding attempt and a host being down
# must not produce the same result.
REFUSED_RESPONSE: Final = _response(
    403, "Forbidden", "nexovue: that address is not one the public internet can reach"
)
UNREACHABLE_RESPONSE: Final = _response(502, "Bad Gateway")
MALFORMED_RESPONSE: Final = _response(400, "Bad Request")


class MalformedRequestError(ValueError):
    """A request head this proxy will not act on."""


@dataclass(frozen=True, slots=True)
class ProxyRequest:
    """One request a browser asked the proxy to make on its behalf."""

    host: str
    port: int
    # None for CONNECT, which carries no head to forward — the client's own
    # bytes start immediately after the tunnel is confirmed.
    forwarded_head: bytes | None
    # How many bytes of request body follow the head. Everything after them on
    # this connection would be a *second* request, and this connection was
    # judged for the first one's host. Meaningless for a tunnel, which is one
    # host's traffic by construction.
    body_bytes: int = 0

    @property
    def is_tunnel(self) -> bool:
        return self.forwarded_head is None


def parse_request(head: bytes) -> ProxyRequest:
    """Read a proxy request head, or refuse to act on it.

    Two shapes reach a proxy and no others: `CONNECT host:port`, which a browser
    uses for everything over TLS, and an absolute-form request line such as
    `GET http://host/path`, which it uses for plain HTTP. Anything else is a
    client talking to us as if we were an origin server, and guessing what it
    meant is how a proxy ends up forwarding somewhere nobody asked for.
    """
    lines = head.split(LINE_TERMINATOR)
    method, target, version = _request_line(lines[0])
    if method.upper() == METHOD_CONNECT:
        host, port = _split_authority(target)
        return ProxyRequest(host=host, port=port, forwarded_head=None)

    parts, named_port = _absolute_form(target)
    if parts.scheme.lower() != SCHEME_HTTP:
        # https and every other scheme arrive as CONNECT or not at all.
        raise MalformedRequestError(f"a proxy request must be http, not {parts.scheme!r}")
    if not parts.hostname:
        raise MalformedRequestError(f"no host in {target!r}")
    header_lines = lines[1:]
    return ProxyRequest(
        host=parts.hostname,
        port=named_port or DEFAULT_HTTP_PORT,
        forwarded_head=_origin_form_head(method, parts, version, header_lines),
        body_bytes=_declared_body_length(header_lines),
    )


def _absolute_form(target: str) -> tuple[SplitResult, int | None]:
    """The URL an absolute-form request line names, and its port.

    `urlsplit` and `SplitResult.port` raise a bare `ValueError` for a URL they
    cannot read — an unclosed IPv6 bracket, a port that is not a number or is
    out of range. `_handle` catches this proxy's own refusal and answers 400;
    it does not catch that, so the browser lost the connection instead of being
    told, and the same input was refused as a CONNECT and leaked as a GET.
    """
    try:
        parts = urlsplit(target)
        port = parts.port
    except ValueError as unreadable:
        raise MalformedRequestError(f"unreadable proxy target {target!r}") from unreadable
    return parts, None if port is None else _port_in_range(port, target)


def _port_in_range(port: int, source: object) -> int:
    """A number that is a port, or a refusal naming where it came from."""
    if not MIN_PORT <= port <= MAX_PORT:
        raise MalformedRequestError(f"{port} is not a port, in {source!r}")
    return port


def _declared_body_length(header_lines: list[bytes]) -> int:
    """How many body bytes the client says follow this head.

    A body we cannot measure is refused rather than relayed blind. Chunked
    framing would need this proxy to parse chunk headers to find where the
    request ends, and two `Content-Length` values is request smuggling's own
    shape — in both cases the proxy cannot say which bytes still belong to the
    host it judged.
    """
    declared: int | None = None
    for line in header_lines:
        name, _, value = line.partition(HEADER_SEPARATOR)
        header = name.strip().lower().decode(HEADER_ENCODING)
        if header == HEADER_TRANSFER_ENCODING:
            raise MalformedRequestError("a proxied request must declare its length, not chunk it")
        if header != HEADER_CONTENT_LENGTH:
            continue
        if declared is not None:
            raise MalformedRequestError("a request may declare its length once")
        declared = _content_length(value)
    return declared or 0


def _content_length(value: bytes) -> int:
    try:
        length = int(value.strip())
    except ValueError as unreadable:
        raise MalformedRequestError(f"unreadable content length {value!r}") from unreadable
    if length < 0:
        raise MalformedRequestError(f"negative content length {length}")
    return length


def _request_line(line: bytes) -> tuple[str, str, str]:
    """Method, target and version — a request line is exactly those three."""
    parts = line.decode(HEADER_ENCODING).split(" ")
    if len(parts) != len(("method", "target", "version")):
        raise MalformedRequestError(f"not a request line: {line!r}")
    method, target, version = parts
    return method, target, version


def _origin_form_head(
    method: str, target: SplitResult, version: str, header_lines: list[bytes]
) -> bytes:
    """The client's head rewritten for the origin: origin-form, one hop's worth."""
    path = target.path or "/"
    if target.query:
        path = f"{path}?{target.query}"

    kept = [line for line in header_lines if line and not _is_hop_by_hop(line)]
    request_line = f"{method} {path} {version}".encode(HEADER_ENCODING)
    return (
        LINE_TERMINATOR.join([request_line, *kept, FORWARDED_CONNECTION_HEADER]) + HEADER_TERMINATOR
    )


def _is_hop_by_hop(header_line: bytes) -> bool:
    name = header_line.split(b":", 1)[0].strip().lower().decode(HEADER_ENCODING)
    return name in HOP_BY_HOP_HEADERS


def _split_authority(authority: str) -> tuple[str, int]:
    """`host:port`, including the bracketed form an IPv6 literal needs.

    There is no default to fall back to: a `CONNECT` with no port names no
    origin, and guessing 443 would be inventing one.
    """
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        port_text = rest.lstrip(":")
    else:
        host, _, port_text = authority.rpartition(":")
        if not host:  # no colon at all
            host, port_text = authority, ""

    if not host:
        raise MalformedRequestError(f"no host in {authority!r}")
    if not port_text:
        raise MalformedRequestError(f"no port in {authority!r}")
    try:
        port = int(port_text)
    except ValueError as bad_port:
        raise MalformedRequestError(f"no port in {authority!r}") from bad_port
    return host, _port_in_range(port, authority)


type Connection = tuple[asyncio.StreamReader, asyncio.StreamWriter]


@dataclass(frozen=True, slots=True)
class OriginAttempt:
    """What came of trying to reach one origin: a connection, or the reason not."""

    connection: Connection | None
    # Why *this* request could not be served, when it could not be — empty once
    # any candidate answered. Its own refusals, not every one the proxy has
    # recorded, so a host being down on one connection is not reported as a
    # refusal because another connection was.
    refused: tuple[str, ...]


class PublicAddressProxy:
    """A forward proxy Chromium cannot reach a private address through.

    Remembers what it refused, so a blocked flow reports *why* rather than
    Chromium's `ERR_TUNNEL_CONNECTION_FAILED`, which reads as the site being
    down — the same reason `ConnectGuard` keeps its own list.
    """

    def __init__(self, *, private_allowed: bool = False) -> None:
        # Passed in for the reason ConnectGuard's is: a hidden environment read
        # inside a security boundary is the kind of thing that survives into
        # production without anyone choosing it.
        self._private_allowed = private_allowed
        self._refused: list[str] = []
        self._refused_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: asyncio.base_events.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self._port: int | None = None

    # ---------------------------------------------------------------- lifecycle

    def __enter__(self) -> PublicAddressProxy:
        self.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> None:
        """Listen on loopback, or raise. A proxy that did not start is not a
        proxy the browser may run without."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._thread = threading.Thread(target=loop.run_forever, name=THREAD_NAME, daemon=True)
        self._thread.start()
        listening = asyncio.run_coroutine_threadsafe(self._listen(), loop)
        try:
            self._port = listening.result(timeout=STARTUP_TIMEOUT_SECONDS)
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop, self._thread, self._port = None, None, None
        if loop is None:
            return
        if self._server is not None:
            asyncio.run_coroutine_threadsafe(self._close_server(), loop).result(
                timeout=SHUTDOWN_TIMEOUT_SECONDS
            )
            self._server = None
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        loop.close()

    async def _listen(self) -> int:
        self._server = await asyncio.start_server(
            self._serve, LISTEN_HOST, EPHEMERAL_PORT, limit=MAX_HEAD_BYTES
        )
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    async def _close_server(self) -> None:
        if self._server is None:  # pragma: no cover - stop() checks first
            return
        self._server.close()
        # Cancelled rather than awaited: a connection an origin never closes
        # would otherwise hold the worker past the flow it was opened for.
        for connection in list(self._connections):
            connection.cancel()
        await self._server.wait_closed()

    # ------------------------------------------------------------------- what it is

    @property
    def endpoint(self) -> str:
        """The address to hand Chromium. Raises if the proxy is not listening."""
        if self._port is None:
            raise RuntimeError("the egress proxy is not listening")
        return f"http://{LISTEN_HOST}:{self._port}"

    def take_refusals(self) -> list[str]:
        """The addresses a request was refused for since last asked, in order.

        Draining, so a caller running steps in sequence can say which step was
        running when a refusal happened. Only approximately: Chromium fetches
        subresources asynchronously, so a refusal caused by one step can land
        while the next is running and be attributed there. What is exact is
        that the connection was refused before it was opened.
        """
        with self._refused_lock:
            refused, self._refused = self._refused, []
        return refused

    def _record_refusals(self, addresses: tuple[str, ...]) -> None:
        with self._refused_lock:
            self._refused.extend(addresses)

    # ---------------------------------------------------------------- serving

    async def _serve(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None  # asyncio guarantees it inside a callback
        self._connections.add(task)
        try:
            await self._handle(client_reader, client_writer)
        except (OSError, asyncio.IncompleteReadError):
            pass  # the browser hung up, or the origin did; neither is ours to report
        finally:
            self._connections.discard(task)
            _close(client_writer)

    async def _handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await client_reader.readuntil(HEADER_TERMINATOR)
            request = parse_request(head[: -len(HEADER_TERMINATOR)])
        except (asyncio.LimitOverrunError, MalformedRequestError):
            await _respond(client_writer, MALFORMED_RESPONSE)
            return

        attempt = await self._open_checked(request.host, request.port)
        if attempt.connection is None:
            failure = REFUSED_RESPONSE if attempt.refused else UNREACHABLE_RESPONSE
            await _respond(client_writer, failure)
            return

        origin_reader, origin_writer = attempt.connection
        try:
            if request.forwarded_head is None:
                client_writer.write(TUNNEL_ESTABLISHED)
                await client_writer.drain()
                await _relay_both_ways(client_reader, client_writer, origin_reader, origin_writer)
            else:
                origin_writer.write(request.forwarded_head)
                await _forward_body(client_reader, origin_writer, request.body_bytes)
                # Nothing more is read from the client. This connection was
                # judged for one host, so it carries one request to it.
                await _pump(origin_reader, client_writer)
        finally:
            _close(origin_writer)

    async def _open_checked(self, host: str, port: int) -> OriginAttempt:
        """Resolve, judge every candidate, connect to the one that was judged.

        The socket is connected to the very tuple `getaddrinfo` returned, so
        nothing re-resolves the name between the ruling and the connection. Each
        candidate is judged separately, exactly as libcurl's socket factory is
        asked once per candidate: a name answering with one public and one
        private address reaches the public one, never touches the private one,
        and is reported as the ordinary success it is.
        """
        loop = asyncio.get_running_loop()
        try:
            candidates = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return OriginAttempt(connection=None, refused=())

        refused: list[str] = []
        for family, socket_type, protocol, _canonical, sockaddr in candidates:
            address = str(sockaddr[0])
            if not may_connect_to(address, private_allowed=self._private_allowed):
                refused.append(address)
                continue
            connection = await _connect(loop, family, socket_type, protocol, sockaddr)
            if connection is not None:
                # Nothing recorded: the request was served and the refused
                # candidates cost it nothing. `http_check` reads its guard's
                # refusals only on the failure path, for the same reason — a
                # name with one private and one public answer is a customer's
                # split-horizon DNS, not an attempt on our network.
                return OriginAttempt(connection=connection, refused=())

        self._record_refusals(tuple(refused))
        return OriginAttempt(connection=None, refused=tuple(refused))


async def _connect(
    loop: asyncio.AbstractEventLoop,
    family: socket.AddressFamily,
    socket_type: socket.SocketKind,
    protocol: int,
    sockaddr: tuple[object, ...],
) -> Connection | None:
    """Connect a fresh socket to one judged address, or report that it did not answer."""
    sock = socket.socket(family, socket_type, protocol)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(loop.sock_connect(sock, sockaddr), timeout=CONNECT_TIMEOUT_SECONDS)
        return await asyncio.open_connection(sock=sock)
    except OSError:
        sock.close()
        return None


async def _forward_body(
    client_reader: asyncio.StreamReader, origin_writer: asyncio.StreamWriter, declared: int
) -> None:
    """Exactly the body the client declared, and not one byte more."""
    remaining = declared
    while remaining > 0:
        chunk = await client_reader.read(min(remaining, RELAY_CHUNK_BYTES))
        if not chunk:  # the client hung up mid-body; the origin sees a short request
            break
        origin_writer.write(chunk)
        await origin_writer.drain()
        remaining -= len(chunk)
    await origin_writer.drain()


async def _relay_both_ways(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    origin_reader: asyncio.StreamReader,
    origin_writer: asyncio.StreamWriter,
) -> None:
    """Copy bytes until either end stops, then let both go."""
    await asyncio.gather(
        _pump(client_reader, origin_writer),
        _pump(origin_reader, client_writer),
        return_exceptions=True,
    )


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(RELAY_CHUNK_BYTES):
            writer.write(chunk)
            await writer.drain()
    finally:
        _close(writer)


async def _respond(writer: asyncio.StreamWriter, response: bytes) -> None:
    writer.write(response)
    await writer.drain()


def _close(writer: asyncio.StreamWriter) -> None:
    if not writer.is_closing():
        writer.close()
