"""F4 plan T2.8: chat's internal client (AC10's offline half). The live half, through real
uvicorn, is `test_path_tricks_are_refused_before_any_read_and_would_move_the_route` in
`apps/api/tests/test_assistant_tools_live.py`, with every catalog tool (T3.1)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from nexovue_platform.assistant_tools import (
    GET_METRIC_HISTOGRAM,
    GET_SERVICE_DETECTION,
    LIST_SERVICES,
    SEARCH_LOGS,
)

from nexovue_scheduler.insight.api_client import (
    CurlApiClient,
    PathRefused,
    build_url,
    check_path_slot,
)


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "a\\b", "a%2Fb", "a?b", "a#b", "a\nb"])
def test_a_path_slot_refuses_anything_that_could_move_the_route(value: str) -> None:
    with pytest.raises(PathRefused):
        check_path_slot(value)


def test_a_plain_name_fits_a_path_slot() -> None:
    check_path_slot("checkout-api_v2.eu")


def test_each_value_is_encoded_whole_under_the_catalog_s_own_path() -> None:
    url = build_url("http://api:8000", SEARCH_LOGS, {"q": "a&b=c /x?", "service": "check out"})
    assert url == "http://api:8000/api/v1/logs/search?q=a%26b%3Dc%20%2Fx%3F&service=check%20out"


def test_a_route_slot_is_filled_with_its_value_encoded_whole() -> None:
    url = build_url(
        "http://api:8000",
        GET_SERVICE_DETECTION,
        {"project_id": "p"},
        {"service_name": "check out"},
    )
    assert url == "http://api:8000/api/v1/services/check%20out/detection?project_id=p"
    dotted = build_url("http://api:8000", GET_METRIC_HISTOGRAM, {}, {"metric_name": "http.dur"})
    assert dotted == "http://api:8000/api/v1/metrics/http.dur/histogram"


@pytest.mark.parametrize(
    "path",
    [
        None,
        {},
        {"service": "checkout"},
        {"service_name": "a", "other": "b"},
        {"service_name": ".."},
    ],
)
def test_a_route_s_slots_must_be_exactly_the_values_given_and_each_safe(
    path: dict[str, str] | None,
) -> None:
    with pytest.raises(PathRefused):
        build_url("http://api:8000", GET_SERVICE_DETECTION, {}, path)


@pytest.mark.parametrize("base", ["ftp://api:8000", "http://", "http://api:8000/?x=1"])
def test_the_base_must_be_a_plain_http_address(base: str) -> None:
    with pytest.raises(PathRefused):
        build_url(base, LIST_SERVICES, {})


class _Recorder(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, str | None]]] = []

    def do_GET(self) -> None:
        self.seen.append((self.path, self.headers.get("Authorization")))
        if self.path.startswith("/api/v1/services"):
            self.send_response(302)
            self.send_header("Location", "/api/v1/logs/search")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"logs": []}')

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def server() -> Iterator[str]:
    _Recorder.seen.clear()
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_a_redirect_is_never_followed_and_the_ticket_rides_a_header(server: str) -> None:
    client = CurlApiClient(server)

    redirected = client.get(LIST_SERVICES, {"from": "a"}, ticket="nvat_x")
    read = client.get(SEARCH_LOGS, {"q": "t"}, ticket="nvat_x")

    assert redirected.status == 302
    assert read.status == 200 and read.body == b'{"logs": []}'
    assert [path for path, _ in _Recorder.seen] == [
        "/api/v1/services?from=a",
        "/api/v1/logs/search?q=t",
    ]
    assert {header for _, header in _Recorder.seen} == {"Bearer nvat_x"}


def test_a_ticket_holding_a_control_character_is_never_sent(server: str) -> None:
    answer = CurlApiClient(server).get(SEARCH_LOGS, {}, ticket="nvat_x\r\nX-Evil: 1")

    assert answer.failure == "not_allowed"
    assert _Recorder.seen == []
