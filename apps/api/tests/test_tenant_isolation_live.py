"""Tenant isolation against the running stack (P1-25).

The offline suite proves the code cannot construct a cross-tenant read. This
one proves the deployed system does not perform one: real Postgres, real
ClickHouse, a second tenant created through the signup endpoint a customer
uses, and a span carried end to end by the ingest path.

It skips itself when the stack is not up, so `just check` still runs without
docker. When the stack *is* up it runs, because an isolation test nobody opts
into is an isolation test nobody runs.

What it creates it removes: every organization it signs up is soft-deleted on
the way out, which is what a deleted tenant looks like to this system. The
spans it ingests are left to the retention TTL — a ClickHouse mutation over
the dev table costs far more than the four rows it would remove, and a
soft-deleted organization can no longer authenticate to read them.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import socket
import statistics
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import asyncpg  # type: ignore[import-untyped]
import httpx
import pytest

from nexovue_api.settings import API_PREFIX

API_ORIGIN: Final = os.environ.get("NEXOVUE_API_ORIGIN", "http://localhost:18000")
RECEIVER_ORIGIN: Final = os.environ.get("NEXOVUE_RECEIVER_ORIGIN", "http://localhost:14318")
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)

# The account `just seed` creates. It is the organization with real traffic in
# it, which makes it the honest "other tenant" to try to read.
SEEDED_EMAIL: Final = os.environ.get("NEXOVUE_SEED_EMAIL", "dev@nexovue.local")
SEEDED_PASSWORD: Final = os.environ.get("NEXOVUE_SEED_PASSWORD", "nexovue-dev")

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0

# The pipeline is receiver → Kafka → trace assembly → ClickHouse, and assembly
# holds a trace until it looks complete. Fourteen seconds is what this stack
# takes; the ceiling is generous so a slow machine fails on a real problem.
INGEST_DEADLINE_SECONDS: Final = 60
INGEST_POLL_SECONDS: Final = 3

# Tail sampling keeps every errored trace whole (ADR 0010) and one healthy
# trace in ten. A probe span therefore reports an error, so that whether it is
# stored is a decision and not a coin toss.
OTLP_STATUS_ERROR: Final = 2
OTLP_KIND_SERVER: Final = 2
PROBE_DURATION_MS: Final = 50
NANOS_PER_MILLISECOND: Final = 1_000_000

# Far enough back that clock skew between this machine and the API cannot put
# the span after the query window's `to`.
PROBE_BACKDATE_MS: Final = 1_000

TRACE_THAT_NEVER_EXISTED: Final = "0" * 32

# Enough rows to make "these two sets are disjoint" mean something, without
# asking the seeded organization's busy project for a page it has to work for.
MAX_PAGE: Final = 50

# Fields that legitimately differ between two answers to the same question.
_PER_REQUEST_FIELDS: Final = frozenset({"instance", "correlation_id"})

# Enough samples for a median to mean something, few enough to stay inside the
# telemetry tier's sixty requests a minute.
TIMING_SAMPLES: Final = 6
# A 404 that costs a scan of another tenant's data would be tens of
# milliseconds slower, not a few. Below this the two answers are the same work.
TIMING_TOLERANCE_SECONDS: Final = 0.05


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection(
            (parts.hostname or "localhost", port), REACHABILITY_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        return False


if not all(_reachable(origin) for origin in (API_ORIGIN, RECEIVER_ORIGIN, POSTGRES_DSN)):
    pytest.skip(
        "the dev stack is not running — start it with `just dev` to run the live "
        "tenant isolation suite",
        allow_module_level=True,
    )


# ------------------------------------------------------------------- the stack


def _control_plane(sql: str, *arguments: object) -> str:
    """One statement against Postgres, for what the API deliberately has no route for."""

    async def run() -> str:
        connection = await asyncpg.connect(POSTGRES_DSN)
        try:
            return str(await connection.execute(sql, *arguments))
        finally:
            await connection.close()

    return asyncio.run(run())


def _soft_delete(org_id: str) -> None:
    _control_plane("UPDATE organizations SET deleted_at = now() WHERE id = $1", uuid.UUID(org_id))


class ApiClient:
    """An httpx client that waits out the rate limiter instead of failing on it.

    The telemetry tier allows sixty requests a minute per address and this
    suite runs sequentially against it; a 429 here is the limiter working, not
    the isolation boundary failing.
    """

    def __init__(self) -> None:
        self._client = httpx.Client(base_url=API_ORIGIN, timeout=REQUEST_TIMEOUT_SECONDS)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, f"{API_PREFIX}{path}", **kwargs)
        if response.status_code == 429:
            time.sleep(float(response.headers.get("retry-after", "1")) + 1)
            response = self._client.request(method, f"{API_PREFIX}{path}", **kwargs)
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True, slots=True)
class LiveTenant:
    """A real organization on the running stack, and a session for it."""

    org_id: str
    project_id: str
    api_key: str
    client: ApiClient


def _sign_up() -> LiveTenant:
    """Create a tenant the way a customer does: the public signup endpoint."""
    client = ApiClient()
    suffix = uuid.uuid4().hex[:12]
    response = client.post(
        "/signup",
        json={
            "organization_name": f"Isolation Suite {suffix}",
            # .test is reserved by RFC 2606 and can never be a real inbox.
            "email": f"p1-25-{suffix}@nexovue.test",
            "password": f"p1-25-{suffix}-password",
        },
    )
    assert response.status_code == 201, response.text

    body = response.json()
    return LiveTenant(
        org_id=body["organization"]["id"],
        project_id=body["project"]["id"],
        api_key=body["api_key"],
        client=client,
    )


def _sign_in_to_the_seeded_organization() -> LiveTenant:
    client = ApiClient()
    response = client.post("/auth/login", json={"email": SEEDED_EMAIL, "password": SEEDED_PASSWORD})
    assert response.status_code == 200, (
        f"the seeded account did not sign in — run `just seed`: {response.text}"
    )
    org_id = response.json()["organization"]["id"]

    projects = client.get("/projects")
    assert projects.status_code == 200, projects.text
    return LiveTenant(
        org_id=org_id,
        project_id=projects.json()["projects"][0]["id"],
        api_key="",
        client=client,
    )


@pytest.fixture(scope="module")
def seeded() -> Iterator[LiveTenant]:
    """The `acme` tenant: the one with other people's traffic in it."""
    tenant = _sign_in_to_the_seeded_organization()
    yield tenant
    tenant.client.close()


@pytest.fixture(scope="module")
def newcomer() -> Iterator[LiveTenant]:
    """A second tenant, signed up for by this suite and deleted after it."""
    tenant = _sign_up()
    yield tenant
    tenant.client.close()
    _soft_delete(tenant.org_id)


# ------------------------------------------------------------------ the ingest


def _export_probe_trace(api_key: str) -> str:
    """Send one errored span through door 1 and return its trace id."""
    trace_id = secrets.token_bytes(16)
    span_id = secrets.token_bytes(8)
    started_ms = int(time.time() * 1000) - PROBE_BACKDATE_MS

    def wire(raw: bytes) -> str:
        # The receiver decodes OTLP/JSON with protojson, which reads `bytes` as
        # base64 rather than the hex the specification shows.
        return base64.b64encode(raw).decode("ascii")

    batch = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "isolation-suite"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "p1-25"},
                        "spans": [
                            {
                                "traceId": wire(trace_id),
                                "spanId": wire(span_id),
                                "name": "GET /probe",
                                "kind": OTLP_KIND_SERVER,
                                "startTimeUnixNano": str(started_ms * NANOS_PER_MILLISECOND),
                                "endTimeUnixNano": str(
                                    (started_ms + PROBE_DURATION_MS) * NANOS_PER_MILLISECOND
                                ),
                                "status": {"code": OTLP_STATUS_ERROR, "message": "probe"},
                            }
                        ],
                    }
                ],
            }
        ]
    }

    response = httpx.post(
        f"{RECEIVER_ORIGIN}/v1/traces",
        json=batch,
        headers={"x-nexovue-key": api_key},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    assert response.status_code == 200, f"the receiver refused the batch: {response.text}"
    return trace_id.hex()


def _wait_until_readable(tenant: LiveTenant, trace_id: str) -> None:
    """Block until the tenant's own session can read the trace it just sent."""
    deadline = time.monotonic() + INGEST_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        response = tenant.client.get(
            f"/traces/{trace_id}", params={"project_id": tenant.project_id}
        )
        if response.status_code == 200:
            return
        time.sleep(INGEST_POLL_SECONDS)
    pytest.fail(f"the probe trace never became readable within {INGEST_DEADLINE_SECONDS}s")


@pytest.fixture(scope="module")
def ingested_trace_id(newcomer: LiveTenant) -> str:
    """A trace that exists, under the newcomer, stamped by its ingest key alone."""
    trace_id = _export_probe_trace(newcomer.api_key)
    _wait_until_readable(newcomer, trace_id)
    return trace_id


def _comparable(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return {key: value for key, value in body.items() if key not in _PER_REQUEST_FIELDS}


# ------------------------------------------------------------------- the tests


def test_a_session_cannot_list_traces_for_another_organizations_project(
    seeded: LiveTenant, newcomer: LiveTenant
) -> None:
    response = seeded.client.get("/traces", params={"project_id": newcomer.project_id})

    assert response.status_code == 404


def test_another_organizations_project_answers_exactly_as_one_that_does_not_exist(
    seeded: LiveTenant, newcomer: LiveTenant
) -> None:
    """A different body would confirm the other organization's project exists."""
    theirs = seeded.client.get("/traces", params={"project_id": newcomer.project_id})
    nobodys = seeded.client.get("/traces", params={"project_id": str(uuid.uuid4())})

    assert theirs.status_code == nobodys.status_code == 404
    assert _comparable(theirs) == _comparable(nobodys)


def test_a_span_ingested_with_one_organizations_key_never_surfaces_under_another(
    seeded: LiveTenant, newcomer: LiveTenant, ingested_trace_id: str
) -> None:
    """The key stamps the tenancy at the edge; nothing downstream may relabel it."""
    mine = newcomer.client.get(
        f"/traces/{ingested_trace_id}", params={"project_id": newcomer.project_id}
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["trace_id"] == ingested_trace_id

    from_their_own_project = seeded.client.get(
        f"/traces/{ingested_trace_id}", params={"project_id": seeded.project_id}
    )
    from_the_owners_project = seeded.client.get(
        f"/traces/{ingested_trace_id}", params={"project_id": newcomer.project_id}
    )

    assert from_their_own_project.status_code == 404
    assert from_the_owners_project.status_code == 404


def test_a_new_organizations_trace_list_holds_nothing_but_its_own(
    newcomer: LiveTenant, ingested_trace_id: str
) -> None:
    """The seeded organization is producing traces continuously beside this one."""
    page = newcomer.client.get(
        "/traces", params={"project_id": newcomer.project_id, "limit": MAX_PAGE}
    )

    assert page.status_code == 200, page.text
    assert {trace["trace_id"] for trace in page.json()["traces"]} == {ingested_trace_id}


def test_a_trace_that_exists_under_another_organization_is_a_404_with_no_timing_tell(
    seeded: LiveTenant, ingested_trace_id: str
) -> None:
    """Body and status already match; a latency difference would be the oracle.

    The two requests take the same path — one lookup on `span_trace_lookup`,
    scoped by tenant, that matches nothing — so their cost must not differ.
    """
    real_but_not_theirs = []
    never_existed = []

    # Interleaved, so a drift in the stack's load cannot land on one sample set.
    for _ in range(TIMING_SAMPLES):
        real_but_not_theirs.append(_time_lookup(seeded, ingested_trace_id))
        never_existed.append(_time_lookup(seeded, TRACE_THAT_NEVER_EXISTED))

    difference = abs(statistics.median(real_but_not_theirs) - statistics.median(never_existed))
    assert difference < TIMING_TOLERANCE_SECONDS, (
        f"another tenant's trace answers {difference * 1000:.1f}ms differently from a "
        f"nonexistent one: {real_but_not_theirs} vs {never_existed}"
    )


def _time_lookup(tenant: LiveTenant, trace_id: str) -> float:
    started = time.perf_counter()
    response = tenant.client.get(f"/traces/{trace_id}", params={"project_id": tenant.project_id})
    elapsed = time.perf_counter() - started
    assert response.status_code == 404, response.text
    return elapsed


def test_a_cursor_issued_to_one_organization_cannot_page_another_organizations_traces(
    seeded: LiveTenant, newcomer: LiveTenant, ingested_trace_id: str
) -> None:
    """The cursor is opaque, not trusted: it names a position inside one tenant."""
    issued = seeded.client.get(
        "/traces", params={"project_id": seeded.project_id, "limit": MAX_PAGE}
    )
    assert issued.status_code == 200, issued.text
    cursor = issued.json()["next_cursor"]
    if cursor is None:
        pytest.skip("the seeded organization has no second page to forge a cursor from")
    theirs = {trace["trace_id"] for trace in issued.json()["traces"]}

    replayed = newcomer.client.get(
        "/traces", params={"project_id": newcomer.project_id, "cursor": cursor}
    )

    assert replayed.status_code == 200, replayed.text
    mine = {trace["trace_id"] for trace in replayed.json()["traces"]}
    assert not mine & theirs, "a cursor issued to one tenant paged another tenant's rows"
    assert mine <= {ingested_trace_id}


def test_a_newcomers_session_cannot_see_the_seeded_organizations_traces(
    seeded: LiveTenant, newcomer: LiveTenant
) -> None:
    """The seeded organization has real traffic; none of it may cross."""
    theirs = seeded.client.get("/traces", params={"project_id": seeded.project_id, "limit": 1})
    assert theirs.status_code == 200, theirs.text
    traces = theirs.json()["traces"]
    if not traces:
        pytest.skip("the seeded organization has no traces in the default window")

    stolen = newcomer.client.get(
        f"/traces/{traces[0]['trace_id']}", params={"project_id": newcomer.project_id}
    )

    assert stolen.status_code == 404


def test_a_session_for_an_organization_that_has_been_deleted_is_refused() -> None:
    """The token outlives the row in Redis, so the identity lookup must not."""
    doomed = _sign_up()
    try:
        assert doomed.client.get("/auth/me").status_code == 200

        _soft_delete(doomed.org_id)

        assert doomed.client.get("/auth/me").status_code == 401
        traces = doomed.client.get("/traces", params={"project_id": doomed.project_id})
        assert traces.status_code == 401
    finally:
        doomed.client.close()
        _soft_delete(doomed.org_id)
