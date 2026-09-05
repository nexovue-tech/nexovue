"""The browser's egress proxy, without a browser.

A raw socket speaks to it exactly as Chromium does — `CONNECT host:port` for
TLS, an absolute-form request line for plain HTTP — so what is asserted is the
proxy's own behaviour rather than Playwright's.

The unit tests need no network. The live ones resolve real names and fetch a
real page, and fail rather than skip when they cannot, for the reason `live.py`
gives: this is the boundary between a customer's URL and our own network.
"""

from __future__ import annotations

import socket
import ssl
from collections.abc import Iterator
from typing import Any, Final

import pytest
from live import require_stack
from loopback_site import LoopbackSite
from proxy_client import (
    ask,
    connect_request,
    get_request,
    open_to,
    read_head,
    read_to_close,
    status_line,
)

from nexovue_scheduler.probes.browser_proxy import (
    DEFAULT_HTTP_PORT,
    LISTEN_HOST,
    MAX_HEAD_BYTES,
    MalformedRequestError,
    PublicAddressProxy,
    parse_request,
)

LOOPBACK_NAME: Final = "127.0.0.1.nip.io"
METADATA_NAME: Final = "169.254.169.254.nip.io"
# Guaranteed never to resolve (RFC 2606), so "unreachable" can be told from "refused".
NONEXISTENT_NAME: Final = "nowhere.invalid"
PUBLIC_HOST: Final = "example.com"
# Reserved for documentation and tests (RFC 2606); the `mixed_answer` fixture is
# the only thing that ever answers for it.
MIXED_ANSWER_NAME: Final = "split-horizon.test"
PRIVATE_ADDRESS: Final = "10.0.0.5"


def _dns_works() -> bool:
    try:
        socket.getaddrinfo(LOOPBACK_NAME, 80)
    except OSError:
        return False
    return True


require_stack(
    reachable=_dns_works(),
    suite="the browser egress proxy's live tests",
    remedy=f"{LOOPBACK_NAME} must resolve, which needs working DNS and internet access",
)


@pytest.fixture
def proxy() -> Iterator[PublicAddressProxy]:
    with PublicAddressProxy() as running:
        yield running


@pytest.fixture
def mixed_answer(monkeypatch: pytest.MonkeyPatch) -> str:
    """A name answering with one private address and then a real public one.

    Split-horizon DNS that leaks an internal A record publicly is an ordinary
    customer misconfiguration, and no public name gives that answer on demand.
    Only the answer is supplied: `_open_checked` still judges every candidate,
    opens a real socket and talks to a real origin.
    """
    resolve = socket.getaddrinfo
    public = resolve(PUBLIC_HOST, DEFAULT_HTTP_PORT, type=socket.SOCK_STREAM)[0]

    def answer(host: str, port: int, *rest: object, **named: object) -> list[Any]:
        if host != MIXED_ANSWER_NAME:
            return resolve(host, port, *rest, **named)  # type: ignore[arg-type]
        family, socket_type, protocol, canonical, sockaddr = public
        return [
            (socket.AF_INET, socket.SOCK_STREAM, protocol, "", (PRIVATE_ADDRESS, port)),
            (family, socket_type, protocol, canonical, sockaddr),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", answer)
    return MIXED_ANSWER_NAME


def _open(proxy: PublicAddressProxy) -> socket.socket:
    return open_to(proxy.endpoint)


def _ask(proxy: PublicAddressProxy, request: bytes) -> bytes:
    return ask(proxy.endpoint, request)


# ------------------------------------------------------------ reading a request


def test_a_connect_request_names_the_host_and_port() -> None:
    request = parse_request(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443")

    assert (request.host, request.port, request.is_tunnel) == ("example.com", 443, True)


def test_a_connect_request_needs_a_port() -> None:
    """A tunnel with no port has no origin; guessing 443 would be inventing one."""
    with pytest.raises(MalformedRequestError):
        parse_request(b"CONNECT example.com HTTP/1.1")


def test_an_ipv6_literal_keeps_its_brackets_apart_from_its_port() -> None:
    request = parse_request(b"CONNECT [2606:4700::1111]:443 HTTP/1.1")

    assert (request.host, request.port) == ("2606:4700::1111", 443)


def test_a_plain_request_is_rewritten_to_origin_form_for_one_hop() -> None:
    """The origin gets a path, not a URL, and exactly one hop's headers."""
    request = parse_request(
        b"GET http://example.com/a/b?q=1 HTTP/1.1\r\n"
        b"Host: example.com\r\n"
        b"Proxy-Connection: keep-alive\r\n"
        b"Connection: keep-alive\r\n"
        b"Accept: */*"
    )

    assert (request.host, request.port, request.is_tunnel) == ("example.com", 80, False)
    assert request.forwarded_head == (
        b"GET /a/b?q=1 HTTP/1.1\r\nHost: example.com\r\nAccept: */*\r\nConnection: close\r\n\r\n"
    )


def test_only_the_body_the_client_declared_follows_the_head() -> None:
    """What comes after a complete request is a second request, and this
    connection was judged for the first one's host."""
    with_body = parse_request(
        b"POST http://example.com/orders HTTP/1.1\r\nHost: example.com\r\nContent-Length: 12"
    )
    without_body = parse_request(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com")

    assert with_body.body_bytes == 12
    assert without_body.body_bytes == 0


@pytest.mark.parametrize(
    "head",
    [
        # Framing we do not read, so we cannot tell the body from what follows it.
        b"POST http://example.com/ HTTP/1.1\r\nTransfer-Encoding: chunked",
        # Two lengths is request smuggling's own shape.
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 9",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: banana",
        b"POST http://example.com/ HTTP/1.1\r\nContent-Length: -1",
    ],
)
def test_a_body_this_proxy_cannot_bound_is_refused(head: bytes) -> None:
    with pytest.raises(MalformedRequestError):
        parse_request(head)


def test_a_plain_request_with_no_path_asks_for_the_root() -> None:
    request = parse_request(b"GET http://example.com:8080 HTTP/1.1\r\nHost: example.com:8080")

    assert request.port == 8080
    assert request.forwarded_head is not None
    assert request.forwarded_head.startswith(b"GET / HTTP/1.1\r\n")


@pytest.mark.parametrize(
    "head",
    [
        b"GET / HTTP/1.1\r\nHost: example.com",  # origin-form: we are not the origin
        b"GET https://example.com/ HTTP/1.1",  # TLS arrives as CONNECT or not at all
        b"GET ftp://example.com/ HTTP/1.1",
        b"GET http:///nohost HTTP/1.1",
        b"NOT A REQUEST",
        b"",
    ],
)
def test_anything_but_a_proxy_request_is_refused_rather_than_guessed(head: bytes) -> None:
    with pytest.raises(MalformedRequestError):
        parse_request(head)


@pytest.mark.parametrize(
    "head",
    [
        b"GET http://example.com:abc/ HTTP/1.1",
        b"GET http://example.com:99999/ HTTP/1.1",
        b"GET http://[::1/ HTTP/1.1",  # unclosed bracket
        b"CONNECT example.com:99999 HTTP/1.1",
        b"CONNECT example.com:0 HTTP/1.1",
    ],
)
def test_a_head_this_proxy_cannot_read_is_refused_as_one(head: bytes) -> None:
    """`MalformedRequestError`, not whatever the stdlib raised.

    `_handle` catches this proxy's own refusal and answers 400. A bare
    `ValueError` from `urlsplit` is not caught anywhere, so the browser gets a
    dropped connection instead of an answer, and the same input is refused on
    one branch and leaks on the other. One rule for both schemes.
    """
    with pytest.raises(MalformedRequestError):
        parse_request(head)


def test_an_unreadable_head_is_answered_rather_than_dropped(proxy: PublicAddressProxy) -> None:
    """The whole point of the parse rule: the connection is answered, not lost."""
    response = _ask(proxy, b"GET http://example.com:99999/ HTTP/1.1\r\nHost: example.com\r\n\r\n")

    assert status_line(response) == "HTTP/1.1 400 Bad Request"
    assert proxy.take_refusals() == []


# ------------------------------------------------------------------ the ruling


def test_the_endpoint_is_loopback_and_only_while_listening() -> None:
    idle = PublicAddressProxy()
    with pytest.raises(RuntimeError):
        _ = idle.endpoint

    with idle:
        assert idle.endpoint.startswith(f"http://{LISTEN_HOST}:")

    with pytest.raises(RuntimeError):
        _ = idle.endpoint


def test_a_tunnel_to_a_loopback_address_is_refused_and_never_opened(
    proxy: PublicAddressProxy, loopback_site: LoopbackSite
) -> None:
    response = _ask(proxy, connect_request(f"127.0.0.1:{loopback_site.port}"))

    assert status_line(response) == "HTTP/1.1 403 Forbidden"
    assert loopback_site.hits == []
    assert proxy.take_refusals() == ["127.0.0.1"]


def test_a_plain_request_to_a_loopback_address_is_refused_and_never_made(
    proxy: PublicAddressProxy, loopback_site: LoopbackSite
) -> None:
    response = _ask(proxy, get_request(loopback_site.url()))

    assert status_line(response) == "HTTP/1.1 403 Forbidden"
    assert loopback_site.hits == []
    assert loopback_site.secret.encode() not in response


def test_the_refusal_page_is_whole() -> None:
    """The body is the length the header promises, and ends in a word."""
    from nexovue_scheduler.probes.browser_proxy import REFUSED_RESPONSE

    head, body = REFUSED_RESPONSE.split(b"\r\n\r\n", 1)
    headers = dict(line.lower().split(b": ", 1) for line in head.split(b"\r\n")[1:])

    assert int(headers[b"content-length"]) == len(body)
    assert body.endswith(b"reach")


@pytest.mark.parametrize(
    "authority",
    [
        "169.254.169.254:80",  # every major cloud's instance credentials
        "10.0.0.5:5432",
        "[::1]:6379",
        "100.100.100.200:80",  # carrier-grade NAT; Alibaba's metadata endpoint
        "0.0.0.0:80",
    ],
)
def test_a_private_literal_is_refused_without_resolving_anything(
    proxy: PublicAddressProxy, authority: str
) -> None:
    response = _ask(proxy, connect_request(authority))

    assert status_line(response) == "HTTP/1.1 403 Forbidden"
    assert proxy.take_refusals() == [authority.rsplit(":", 1)[0].strip("[]")]


def test_localhost_is_resolved_and_then_refused(
    proxy: PublicAddressProxy, loopback_site: LoopbackSite
) -> None:
    """A name is never trusted; what it resolves to is what is judged."""
    response = _ask(proxy, connect_request(f"localhost:{loopback_site.port}"))

    assert status_line(response) == "HTTP/1.1 403 Forbidden"
    assert loopback_site.hits == []
    assert set(proxy.take_refusals()) <= {"127.0.0.1", "::1"}


def test_refusals_are_handed_over_once(proxy: PublicAddressProxy) -> None:
    _ask(proxy, connect_request("10.0.0.5:443"))

    assert proxy.take_refusals() == ["10.0.0.5"]
    assert proxy.take_refusals() == []


def test_a_head_larger_than_a_browser_sends_is_refused_unread(proxy: PublicAddressProxy) -> None:
    with _open(proxy) as sock:
        sock.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nX-Filler: " + b"a" * MAX_HEAD_BYTES)
        response = read_to_close(sock)

    assert status_line(response) == "HTTP/1.1 400 Bad Request"
    assert proxy.take_refusals() == []


def test_talking_to_the_proxy_as_if_it_were_an_origin_is_refused(
    proxy: PublicAddressProxy,
) -> None:
    response = _ask(proxy, b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")

    assert status_line(response) == "HTTP/1.1 400 Bad Request"


# -------------------------------------------------------------------- live DNS


@pytest.mark.parametrize(
    ("name", "address"), [(LOOPBACK_NAME, "127.0.0.1"), (METADATA_NAME, "169.254.169.254")]
)
def test_a_public_name_answering_privately_is_refused_at_the_resolved_address(
    proxy: PublicAddressProxy, name: str, address: str
) -> None:
    """The rebinding case: a public name, judged by what it resolves to now."""
    response = _ask(proxy, connect_request(f"{name}:443"))

    assert status_line(response) == "HTTP/1.1 403 Forbidden"
    assert proxy.take_refusals() == [address]


def test_a_public_name_answering_loopback_never_reaches_the_loopback_service(
    proxy: PublicAddressProxy, loopback_site: LoopbackSite
) -> None:
    response = _ask(proxy, get_request(loopback_site.url(LOOPBACK_NAME)))

    assert status_line(response) == "HTTP/1.1 403 Forbidden"
    assert loopback_site.hits == []


def test_a_name_that_does_not_exist_is_unreachable_not_refused(proxy: PublicAddressProxy) -> None:
    """502 is "nothing answered"; 403 is "we would not ask". They must differ."""
    response = _ask(proxy, connect_request(f"{NONEXISTENT_NAME}:443"))

    assert status_line(response) == "HTTP/1.1 502 Bad Gateway"
    assert proxy.take_refusals() == []


def test_one_connections_refusal_does_not_colour_another(proxy: PublicAddressProxy) -> None:
    """Judged per request: an earlier refusal must not turn a later outage into one."""
    assert status_line(_ask(proxy, connect_request("10.0.0.5:443"))) == "HTTP/1.1 403 Forbidden"

    later = _ask(proxy, connect_request(f"{NONEXISTENT_NAME}:443"))

    assert status_line(later) == "HTTP/1.1 502 Bad Gateway"


def test_a_refused_candidate_is_not_reported_when_another_one_answered(
    proxy: PublicAddressProxy, mixed_answer: str
) -> None:
    """A name answering with both a private and a public address.

    Only the DNS answer is supplied here; the judgement, the socket and the
    origin are all real. The private candidate is first, so it is judged and
    skipped, and the request is served by the public one — which means nothing
    was refused *that mattered*. `http_check` reports the same way: its
    `refusal_reason()` is consulted only when the request failed. Reporting it
    here made the same target pass as an HTTP check and fail as a browser one.
    """
    response = _ask(proxy, get_request(f"http://{mixed_answer}/"))

    status = status_line(response)
    assert status.startswith("HTTP/1.1 "), status
    assert status.split(" ")[1] not in ("403", "502"), status
    assert proxy.take_refusals() == []


def test_a_plain_request_to_a_public_site_is_relayed_and_then_closed(
    proxy: PublicAddressProxy,
) -> None:
    response = _ask(proxy, get_request(f"http://{PUBLIC_HOST}/"))

    status = status_line(response)
    assert status.startswith("HTTP/1.1 ") and status.split(" ")[1][0] in "23", status
    assert proxy.take_refusals() == []


def test_a_tunnel_to_a_public_site_carries_tls_end_to_end(proxy: PublicAddressProxy) -> None:
    """CONNECT, then a real handshake with the origin through the tunnel."""
    with _open(proxy) as sock:
        sock.sendall(connect_request(f"{PUBLIC_HOST}:443"))
        assert status_line(read_head(sock)) == "HTTP/1.1 200 Connection Established"

        with ssl.create_default_context().wrap_socket(sock, server_hostname=PUBLIC_HOST) as tls:
            tls.sendall(get_request(f"https://{PUBLIC_HOST}/").replace(b"https://example.com", b""))
            status = status_line(read_head(tls))

    assert status.startswith("HTTP/1.1 ") and status.split(" ")[1][0] in "23", status
    assert proxy.take_refusals() == []
