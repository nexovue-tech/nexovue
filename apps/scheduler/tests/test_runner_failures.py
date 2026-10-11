"""Misconfigured and prober errors are told apart from the target being down,
and no secret survives into anything a run leaves behind (AC17, AC22, EC2)."""

from __future__ import annotations

from nexovue_platform.checks.task import FileReference
from probe_fixtures import MemoryFiles, context, definition, status_below_400
from recording_site import RecordingSite, Reply, json_reply

from nexovue_scheduler.probes import runner
from nexovue_scheduler.probes.redaction import REDACTED, Redactor

MARKER = "marker-9c1e-secret-value"


def test_an_undefined_variable_is_misconfigured_and_nothing_is_sent(
    recording_site: RecordingSite,
) -> None:
    check = definition(
        {
            "type": "api",
            "request": {
                "url": recording_site.url("/{{missing}}"),
                "assertions": [status_below_400()],
            },
        }
    )
    evidence, measurement = runner.run(check, context())

    assert evidence.outcome == "misconfigured"
    assert not measurement.up
    assert evidence.error is not None and "{{missing}} is not defined" in evidence.error
    assert recording_site.requests == []
    # Nothing was sent, so nothing was timed. Zero would be a real, instant answer.
    assert evidence.duration_ms is None
    assert measurement.total_seconds is None


def test_a_deleted_file_is_misconfigured() -> None:
    check = definition(
        {
            "type": "api",
            "request": {
                "method": "PUT",
                "url": "https://files.example.com/",
                "body": {"kind": "binary", "file_id": "00000000-0000-0000-0000-00000000000f"},
                "assertions": [status_below_400()],
            },
        },
        files={
            "00000000-0000-0000-0000-00000000000f": FileReference(
                name="a", content_type="x", size_bytes=1
            )
        },
    )
    evidence, _ = runner.run(check, context(MemoryFiles()))
    assert evidence.outcome == "misconfigured"


def test_a_prober_without_the_secret_key_reports_its_own_error() -> None:
    check = definition(
        {
            "type": "api",
            "request": {
                "url": "https://a.example/",
                "auth": {"kind": "bearer", "token_secret": "T"},
                "assertions": [status_below_400()],
            },
        },
        secrets={"T": MARKER},
    )
    evidence, _ = runner.run(check, context(secret_key=""))
    assert evidence.outcome == "prober_error"


def test_a_secret_is_redacted_from_every_part_of_the_evidence(
    recording_site: RecordingSite,
) -> None:
    """AC17, prober side. The target echoes the secret back in its body, a header
    and a JSON field; the URL, a header and the body all carried it out."""
    recording_site.replies["/echo"] = json_reply({"echo": MARKER, "quoted": f'a"{MARKER}'})
    check = definition(
        {
            "type": "api",
            "request": {
                "method": "POST",
                "url": recording_site.url("/echo?key={{secret.KEY}}"),
                "headers": [{"name": "X-Key", "value": "{{secret.KEY}}"}],
                "body": {"kind": "json", "content": '{"key": "{{secret.KEY}}"}'},
                "assertions": [
                    {
                        "source": "json_path",
                        "property": "$.echo",
                        "comparison": "equals",
                        "target": "nope",
                    }
                ],
            },
        },
        secrets={"KEY": MARKER},
    )

    evidence, _ = runner.run(check, context())

    received = recording_site.requests[-1]
    assert MARKER in received.headers["x-key"], "the secret must still reach the target"
    serialized = evidence.model_dump_json()
    assert MARKER not in serialized
    assert REDACTED in serialized
    assert evidence.outcome == "down"
    assert evidence.error is not None and MARKER not in evidence.error


def test_redaction_catches_the_encoded_forms_a_secret_leaves_in() -> None:
    redactor = Redactor(["p@ss word"])
    assert redactor.text("q=p%40ss%20word&r=p%40ss+word") == f"q={REDACTED}&r={REDACTED}"
    assert redactor.text("Basic cEBzcyB3b3Jk") == f"Basic {REDACTED}"


def test_a_short_value_is_not_redacted_everywhere_it_appears() -> None:
    assert Redactor(["ab"]).text("cab") == "cab"


def test_a_body_nested_past_the_interpreters_depth_is_not_json_not_a_crash(
    recording_site: RecordingSite,
) -> None:
    """Found in review: `json.loads` raised RecursionError and the run recorded nothing."""
    recording_site.replies["/deep"] = Reply(body=b"[" * 200_000 + b"]" * 200_000)
    check = definition(
        {
            "type": "api",
            "request": {
                "url": recording_site.url("/deep"),
                "assertions": [
                    {"source": "json_path", "property": "$[0]", "comparison": "is_not_empty"}
                ],
            },
        }
    )
    evidence, _ = runner.run(check, context())
    assert evidence.outcome == "down"
    assert evidence.error is not None and "got nothing" in evidence.error


def test_a_non_ascii_url_is_sent_encoded(recording_site: RecordingSite) -> None:
    """Found in review: pycurl raised UnicodeEncodeError for `José` in a path."""
    check = definition(
        {
            "type": "api",
            "request": {
                "url": recording_site.url("/people/{{who}}"),
                "assertions": [status_below_400()],
            },
        },
        variables={"who": "José"},
    )
    evidence, _ = runner.run(check, context())
    assert evidence.outcome == "up"
    assert recording_site.requests[-1].path == "/people/Jos%C3%A9"


def test_a_secret_straddling_the_evidence_cut_leaves_no_fragment(
    recording_site: RecordingSite,
) -> None:
    """Found in review: redaction ran after the 16 KiB cut, so half a token survived."""
    secret = "tok_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    padding = b"a" * (16 * 1024 - 10)
    recording_site.replies["/echo"] = Reply(body=padding + secret.encode(), headers={})
    check = definition(
        {
            "type": "api",
            "request": {
                "url": recording_site.url("/echo"),
                "headers": [{"name": "X-Key", "value": "{{secret.KEY}}"}],
                "assertions": [status_below_400()],
            },
        },
        secrets={"KEY": secret},
    )
    evidence, _ = runner.run(check, context())
    text = evidence.model_dump_json()
    assert "tok_ABCDEF" not in text


def test_a_variable_in_an_assertion_target_is_filled_in(recording_site: RecordingSite) -> None:
    """Found in review: the target was compared as the literal `{{expected}}`."""
    recording_site.replies["/v"] = json_reply({"version": "2.4.1"})
    check = definition(
        {
            "type": "api",
            "request": {
                "url": recording_site.url("/v"),
                "assertions": [
                    {
                        "source": "json_path",
                        "property": "$.version",
                        "comparison": "equals",
                        "target": "{{expected}}",
                    }
                ],
            },
        },
        variables={"expected": "2.4.1"},
    )
    evidence, _ = runner.run(check, context())
    assert evidence.outcome == "up"


def test_evidence_too_large_to_publish_loses_bodies_and_says_so() -> None:
    from datetime import UTC, datetime

    from nexovue_platform.checks.evidence import (
        CapturedBody,
        ReceivedResponse,
        RunEvidence,
        StepEvidence,
    )

    from nexovue_scheduler.probes.fitting import MAX_EVIDENCE_BYTES, fitted

    body = CapturedBody(text="x" * 16_000, size_bytes=16_000, truncated=False)
    evidence = RunEvidence(
        run_id="r",
        check_id="c",
        check_type="multistep",
        location="local",
        attempt=1,
        final=True,
        outcome="down",
        started_at=datetime.now(UTC),
        duration_ms=1.0,
        steps=[
            StepEvidence(
                position=index,
                target="https://a.example",
                response=ReceivedResponse(status_code=500, body=body),
                passed=False,
            )
            for index in range(40)
        ],
        error="expected status code less than 400, got 500",
    )
    small = fitted(evidence)
    assert len(small.model_dump_json()) <= MAX_EVIDENCE_BYTES
    assert small.outcome == "down"
    assert small.error is not None and small.error.startswith("expected status code")
    assert "too large" in small.error


def test_a_secret_sealed_before_a_rotation_still_opens_on_the_prober() -> None:
    """Blocker 8. A job queued before the rotation carries a value sealed under the
    old key; the prober holds it in CHECK_SECRET_PREVIOUS_KEYS until those drain."""
    from nexovue_platform.checks.sealing import ConfiguredKeys, generate_key
    from probe_fixtures import PROJECT_ID, SECRET_KEY, sealed

    from nexovue_scheduler.probes.secrets import open_secrets

    opened = open_secrets(
        sealed({"T": MARKER}),
        project_id=PROJECT_ID,
        keys=ConfiguredKeys(generate_key(), previous=SECRET_KEY),
    )

    assert opened == {"T": MARKER}
