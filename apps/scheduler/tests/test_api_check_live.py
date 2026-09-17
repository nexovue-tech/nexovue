"""API checks against a real HTTP server: what was sent is what was configured.

Live in the sense that matters — pycurl, a socket, a server reading bytes — and
needing nothing but this machine. Each test asserts on what the recording site
*received*, not on what the probe believes it sent (AC1, AC2, AC3, AC4, AC5).
"""

from __future__ import annotations

import base64
import json

from nexovue_platform.checks.task import FileReference
from probe_fixtures import MemoryFiles, context, definition, status_below_400
from recording_site import RecordingSite, Reply, json_reply

from nexovue_scheduler.probes import runner


def _api(request: dict[str, object]) -> dict[str, object]:
    return {"type": "api", "request": request}


def test_any_method_headers_query_and_a_json_body_arrive_as_configured(
    recording_site: RecordingSite,
) -> None:
    """AC1, with a method outside the common seven."""
    check = definition(
        _api(
            {
                "method": "PROPFIND",
                "url": recording_site.url("/orders"),
                "query": [{"name": "page", "value": "2"}, {"name": "off", "enabled": False}],
                "headers": [{"name": "X-Trace", "value": "abc"}],
                "body": {"kind": "json", "content": '{"item": "shoe", "qty": 2}'},
                "assertions": [status_below_400()],
            }
        )
    )

    evidence, measurement = runner.run(check, context())

    received = recording_site.requests[-1]
    assert received.method == "PROPFIND"
    assert received.path == "/orders?page=2"
    assert received.headers["x-trace"] == "abc"
    assert received.headers["content-type"] == "application/json"
    assert json.loads(received.body) == {"item": "shoe", "qty": 2}
    assert evidence.outcome == "up"
    assert measurement.status_code == 200
    assert "user-agent" in received.headers


def test_a_post_with_a_form_body_is_urlencoded(recording_site: RecordingSite) -> None:
    check = definition(
        _api(
            {
                "method": "POST",
                "url": recording_site.url("/login"),
                "body": {"kind": "form", "fields": [{"name": "user", "value": "a b"}]},
                "assertions": [status_below_400()],
            }
        )
    )
    runner.run(check, context())

    received = recording_site.requests[-1]
    assert received.method == "POST"
    assert received.body == b"user=a+b"
    assert received.headers["content-type"] == "application/x-www-form-urlencoded"


def test_multipart_fields_and_files_arrive_byte_for_byte(recording_site: RecordingSite) -> None:
    """AC2. The file comes from the file source, never from the worker's disk."""
    payload = bytes(range(256)) * 8200  # just over 2 MB, every byte value
    files = MemoryFiles({"f1": payload})
    check = definition(
        _api(
            {
                "method": "POST",
                "url": recording_site.url("/upload"),
                "body": {
                    "kind": "multipart",
                    "parts": [
                        {"kind": "field", "name": "note", "value": "hello"},
                        {
                            "kind": "file",
                            "name": "report",
                            "file_id": "00000000-0000-0000-0000-000000000001",
                        },
                    ],
                },
                "assertions": [status_below_400()],
            }
        ),
        files={
            "00000000-0000-0000-0000-000000000001": FileReference(
                name="report.bin", content_type="application/octet-stream", size_bytes=len(payload)
            )
        },
    )
    files.files["00000000-0000-0000-0000-000000000001"] = payload

    evidence, _ = runner.run(check, context(files))

    received = recording_site.requests[-1]
    assert received.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="note"\r\n\r\nhello\r\n' in received.body
    assert b'filename="report.bin"' in received.body
    assert payload in received.body
    # The evidence describes the file; it does not carry two megabytes of it.
    request_body = evidence.steps[0].request.body if evidence.steps[0].request else None
    assert request_body is not None and len(request_body.text) < 1000


def test_a_binary_body_is_sent_with_its_content_type(recording_site: RecordingSite) -> None:
    files = MemoryFiles({"00000000-0000-0000-0000-000000000002": b"\x00\x01\xff"})
    check = definition(
        _api(
            {
                "method": "PUT",
                "url": recording_site.url("/blob"),
                "body": {
                    "kind": "binary",
                    "file_id": "00000000-0000-0000-0000-000000000002",
                    "content_type": "image/png",
                },
                "assertions": [status_below_400()],
            }
        )
    )
    runner.run(check, context(files))

    received = recording_site.requests[-1]
    assert (received.method, received.body) == ("PUT", b"\x00\x01\xff")
    assert received.headers["content-type"] == "image/png"


def test_a_200_whose_json_fails_an_assertion_is_down_with_expected_and_actual(
    recording_site: RecordingSite,
) -> None:
    """AC3."""
    recording_site.replies["/order"] = json_reply({"order": {"id": None, "total": 12.5}})
    check = definition(
        _api(
            {
                "url": recording_site.url("/order"),
                "assertions": [
                    {"source": "status_code", "comparison": "equals", "target": "200"},
                    {"source": "json_path", "property": "$.order.id", "comparison": "is_not_empty"},
                ],
            }
        )
    )

    evidence, measurement = runner.run(check, context())

    assert evidence.outcome == "down"
    failed = [result for result in evidence.steps[0].assertions if not result.passed]
    assert len(failed) == 1
    assert (failed[0].property, failed[0].actual) == ("$.order.id", "null")
    assert evidence.error == "expected json path $.order.id is not empty, got null"
    assert measurement.status_code == 200
    assert evidence.steps[0].response is not None
    assert evidence.steps[0].response.status_code == 200


def test_a_value_extracted_from_one_request_reaches_the_next(recording_site: RecordingSite) -> None:
    """AC4, first half."""
    recording_site.replies["/login"] = json_reply({"token": "t-123"})
    check = definition(
        {
            "type": "multistep",
            "steps": [
                {
                    "method": "POST",
                    "url": recording_site.url("/login"),
                    "assertions": [status_below_400()],
                    "extractions": [
                        {"name": "token", "source": "json_path", "expression": "$.token"}
                    ],
                },
                {
                    "url": recording_site.url("/me"),
                    "headers": [{"name": "X-Token", "value": "{{token}}"}],
                    "assertions": [status_below_400()],
                },
            ],
        }
    )

    evidence, _ = runner.run(check, context())

    assert evidence.outcome == "up"
    assert recording_site.requests[-1].headers["x-token"] == "t-123"
    assert evidence.steps[0].extracted == ["token"]


def test_a_failure_in_the_second_request_is_reported_against_it(
    recording_site: RecordingSite,
) -> None:
    """AC4, second half."""
    recording_site.replies["/second"] = Reply(status=503, body=b"busy")
    check = definition(
        {
            "type": "multistep",
            "steps": [
                {"url": recording_site.url("/first"), "assertions": [status_below_400()]},
                {"url": recording_site.url("/second"), "assertions": [status_below_400()]},
                {"url": recording_site.url("/third"), "assertions": [status_below_400()]},
            ],
        }
    )

    evidence, _ = runner.run(check, context())

    assert evidence.outcome == "down"
    assert [step.passed for step in evidence.steps] == [True, False]
    assert evidence.error is not None and evidence.error.startswith("request 2:")
    assert [request.path for request in recording_site.requests] == ["/first", "/second"]


def test_a_redirect_to_a_private_address_is_refused_at_that_hop(
    recording_site: RecordingSite,
) -> None:
    """AC5. Private addresses are allowed in this suite, so the hop goes to the
    metadata address, which is refused at every setting."""
    recording_site.replies["/go"] = Reply(
        status=302, body=b"", headers={"Location": "http://169.254.169.254/latest/meta-data/"}
    )
    check = definition(_api({"url": recording_site.url("/go"), "assertions": [status_below_400()]}))

    evidence, _ = runner.run(check, context())

    assert evidence.outcome == "down"
    assert evidence.error is not None
    assert "not contacted" in evidence.error and "169.254.169.254" not in evidence.error


def test_basic_auth_uses_the_secret_and_the_secret_never_appears(
    recording_site: RecordingSite,
) -> None:
    """The header arrives, and the evidence withholds it."""
    marker = "s3cr3t-marker-7f2a"
    check = definition(
        _api(
            {
                "url": recording_site.url("/private"),
                "auth": {"kind": "basic", "username": "ops", "password_secret": "PASSWORD"},
                "assertions": [status_below_400()],
            }
        ),
        secrets={"PASSWORD": marker},
    )

    evidence, _ = runner.run(check, context())

    expected = "Basic " + base64.b64encode(f"ops:{marker}".encode()).decode()
    assert recording_site.requests[-1].headers["authorization"] == expected
    assert marker not in evidence.model_dump_json()


def test_the_degraded_and_failing_thresholds_judge_a_passing_run(
    recording_site: RecordingSite,
) -> None:
    """AC21: every assertion passing and slower than the degraded threshold is
    degraded; slower than the failing one is down."""
    recording_site.replies["/slow"] = Reply(delay=0.2)
    config = _api({"url": recording_site.url("/slow"), "assertions": [status_below_400()]})

    fast_enough, _ = runner.run(definition(config, degraded_ms=5000), context())
    degraded, measurement = runner.run(definition(config, degraded_ms=50), context())
    failing, _ = runner.run(definition(config, degraded_ms=50, failing_ms=100), context())

    assert fast_enough.outcome == "up"
    assert degraded.outcome == "degraded"
    assert measurement.up, "degraded is up for uptime; it is slow, not broken"
    assert failing.outcome == "down"
    assert failing.error is not None and "failing threshold of 100 ms" in failing.error


def test_a_body_past_the_cap_is_cut_and_says_so(recording_site: RecordingSite) -> None:
    recording_site.replies["/big"] = Reply(body=b"a" * (2 * 1024 * 1024), headers={})
    check = definition(
        _api(
            {
                "url": recording_site.url("/big"),
                "assertions": [{"source": "body", "comparison": "contains", "target": "aaa"}],
            }
        )
    )

    evidence, _ = runner.run(check, context())

    response = evidence.steps[0].response
    assert evidence.outcome == "up"
    assert response is not None and response.body is not None
    assert response.body.truncated
    assert len(response.body.text) <= 16 * 1024
