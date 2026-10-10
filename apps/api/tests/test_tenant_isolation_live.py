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
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
from live import require_stack
from pydantic import SecretStr

from nexovue_api.settings import API_PREFIX

API_ORIGIN: Final = os.environ.get("NEXOVUE_API_ORIGIN", "http://localhost:18000")
RECEIVER_ORIGIN: Final = os.environ.get("NEXOVUE_RECEIVER_ORIGIN", "http://localhost:14318")
POSTGRES_DSN: Final = os.environ.get(
    "NEXOVUE_POSTGRES_DSN", "postgresql://nexovue:nexovue_dev@localhost:15432/nexovue"
)

# The account `just seed` creates. It is the organization with real traffic in
# it, which makes it the honest "other tenant" to try to read.
SEEDED_EMAIL: Final = os.environ.get("NEXOVUE_SEED_EMAIL", "nexovue@nexovue.com")
SEEDED_PASSWORD: Final = os.environ.get("NEXOVUE_SEED_PASSWORD", "nexovue-dev")

REACHABILITY_TIMEOUT_SECONDS: Final = 1.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0

# The pipeline is receiver → Kafka → trace assembly → ClickHouse, and assembly
# holds a trace until it looks complete. Fourteen seconds is what this stack
# takes; the ceiling is generous so a slow machine fails on a real problem.
INGEST_DEADLINE_SECONDS: Final = 60
INGEST_POLL_SECONDS: Final = 3

# Tail sampling keeps every errored trace whole and one healthy
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


require_stack(
    reachable=all(_reachable(origin) for origin in (API_ORIGIN, RECEIVER_ORIGIN, POSTGRES_DSN)),
    suite="the live tenant isolation suite",
    remedy="the dev stack is not running — start it with `just dev`",
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
    """A real organization on the running stack, and a session for it.

    `api_key` is a `SecretStr` because this dataclass's repr is printed verbatim
    by pytest on any failing assertion that holds one — which put a working
    ingest key into a test log. The redaction travels with the value rather than
    depending on this declaration: pass the wrapper around, and only
    `get_secret_value()` at the moment it goes into a header discloses it.
    """

    org_id: str
    project_id: str
    api_key: SecretStr
    client: ApiClient


# Signup sits behind the per-IP sliding window every other auth route shares, and
# the whole test suite is one IP. Running this file alone stays well inside it;
# running it after the rest of the suite does not, and the failure then surfaces
# somewhere confusing — a later request answering 401 because its session was
# never created. Waiting is correct: the limit is real and the suite is what
# should yield to it.
_RATE_LIMIT_ATTEMPTS = 4
_RATE_LIMIT_PAUSE_SECONDS = 20


def _sign_up() -> LiveTenant:
    """Create a tenant the way a customer does: the public signup endpoint."""
    client = ApiClient()
    suffix = uuid.uuid4().hex[:12]
    for attempt in range(1, _RATE_LIMIT_ATTEMPTS + 1):
        response = client.post(
            "/signup",
            json={
                "organization_name": f"Isolation Suite {suffix}",
                # .test is reserved by RFC 2606 and can never be a real inbox.
                "email": f"p1-25-{suffix}@nexovue.test",
                "password": f"p1-25-{suffix}-password",
            },
        )
        if response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
            break
        assert attempt < _RATE_LIMIT_ATTEMPTS, (
            "signup stayed rate limited across "
            f"{_RATE_LIMIT_ATTEMPTS} attempts; the suite is outrunning the per-IP "
            "auth window, not finding an isolation defect"
        )
        time.sleep(_RATE_LIMIT_PAUSE_SECONDS)

    assert response.status_code == 201, response.text

    body = response.json()
    return LiveTenant(
        org_id=body["organization"]["id"],
        project_id=body["project"]["id"],
        api_key=SecretStr(body["api_key"]),
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
        api_key=SecretStr(""),
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


def _export_probe_trace(api_key: SecretStr) -> str:
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
        # The only disclosure: the header the receiver authenticates against.
        headers={"x-nexovue-key": api_key.get_secret_value()},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    assert response.status_code == 200, f"the receiver refused the batch: {response.text}"
    return trace_id.hex()


def _wait_until_readable(tenant: LiveTenant, trace_id: str) -> None:
    """Block until the tenant can read the trace it just sent — by id *and* in the list.

    **Both, because they are two different writes.** Fetching one trace reads the
    span rows; listing traces reads the summary the assembler derives from them,
    and the second lands after the first. Waiting only for the fetch and then
    asserting on the list is waiting for the wrong thing — which is how
    `test_a_new_organizations_trace_list_holds_nothing_but_its_own` failed under
    the parallel gate on 2026-09-21 with an empty set, on a trace that was
    already fetchable by id.

    The suite had been serial, where the summary happened to land inside the
    first poll. Six workers made the stack busy enough for the gap to show.
    """
    deadline = time.monotonic() + INGEST_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        fetched = tenant.client.get(f"/traces/{trace_id}", params={"project_id": tenant.project_id})
        if fetched.status_code == 200 and _appears_in_the_list(tenant, trace_id):
            return
        time.sleep(INGEST_POLL_SECONDS)
    pytest.fail(
        f"the probe trace never became both fetchable and listable within "
        f"{INGEST_DEADLINE_SECONDS}s"
    )


def _appears_in_the_list(tenant: LiveTenant, trace_id: str) -> bool:
    """Whether the trace has reached the summary the list is served from."""
    listed = tenant.client.get(
        "/traces", params={"project_id": tenant.project_id, "limit": MAX_PAGE}
    )
    if listed.status_code != 200:
        return False
    return any(trace["trace_id"] == trace_id for trace in listed.json()["traces"])


@pytest.fixture(scope="module")
def ingested_trace_id(newcomer: LiveTenant) -> str:
    """A trace that exists, under the newcomer, stamped by its ingest key alone."""
    trace_id = _export_probe_trace(newcomer.api_key)
    _wait_until_readable(newcomer, trace_id)
    return trace_id


PROFILE_SERVICE: Final = "isolation-suite-profiled"


def _export_probe_profile(api_key: SecretStr) -> None:
    """Send one CPU sample through the profiles door (F17, AC7): a two-frame stack
    under a service name no other tenant uses, as OTLP/JSON."""
    now_ns = (int(time.time() * 1000) - PROBE_BACKDATE_MS) * NANOS_PER_MILLISECOND
    request = {
        "dictionary": {
            "stringTable": ["", "cpu", "nanoseconds", "isolation.probe", "main"],
            "functionTable": [{}, {"nameStrindex": 3}, {"nameStrindex": 4}],
            "locationTable": [
                {},
                {"lines": [{"functionIndex": 1}]},
                {"lines": [{"functionIndex": 2}]},
            ],
            "stackTable": [{}, {"locationIndices": [1, 2]}],
        },
        "resourceProfiles": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": PROFILE_SERVICE}}
                    ]
                },
                "scopeProfiles": [
                    {
                        "profiles": [
                            {
                                "sampleType": {"typeStrindex": 1, "unitStrindex": 2},
                                "timeUnixNano": str(now_ns),
                                "samples": [{"stackIndex": 1, "values": ["10000000"]}],
                            }
                        ]
                    }
                ],
            }
        ],
    }
    response = httpx.post(
        f"{RECEIVER_ORIGIN}/v1development/profiles",
        json=request,
        headers={"x-nexovue-key": api_key.get_secret_value()},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    assert response.status_code == 200, f"the receiver refused the profile: {response.text}"


def _profiled_services(tenant: LiveTenant, project_id: str) -> httpx.Response:
    return tenant.client.get("/profiles/services", params={"project_id": project_id})


@pytest.fixture(scope="module")
def ingested_profile(newcomer: LiveTenant) -> None:
    """A profile that exists, under the newcomer, stamped by its ingest key alone."""
    _export_probe_profile(newcomer.api_key)
    deadline = time.monotonic() + INGEST_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        response = _profiled_services(newcomer, newcomer.project_id)
        assert response.status_code == 200, response.text
        if any(s["service_name"] == PROFILE_SERVICE for s in response.json()["services"]):
            return
        time.sleep(INGEST_POLL_SECONDS)
    pytest.fail(f"the probe profile never became readable within {INGEST_DEADLINE_SECONDS}s")


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
        pytest.fail(
            "the seeded organization has only one page of traces, so a forged "
            "cursor cannot be tested — send some traffic with `just otel-demo up` "
            "or `just load`"
        )
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
        pytest.fail(
            "the seeded organization has no traces in the default window, so "
            "cross-tenant trace reads cannot be tested — send some traffic with "
            "`just otel-demo up` or `just load`"
        )

    stolen = newcomer.client.get(
        f"/traces/{traces[0]['trace_id']}", params={"project_id": newcomer.project_id}
    )

    assert stolen.status_code == 404


def test_a_session_for_an_organization_that_has_been_deleted_is_refused() -> None:
    """The session outlives the row, so the identity lookup must not."""
    doomed = _sign_up()
    try:
        # The body, not just the code: the three 401s this endpoint can answer
        # mean different things, and a bare status hides which one arrived.
        before = doomed.client.get("/auth/me")
        assert before.status_code == 200, before.text

        _soft_delete(doomed.org_id)

        after = doomed.client.get("/auth/me")
        assert after.status_code == 401, after.text
        traces = doomed.client.get("/traces", params={"project_id": doomed.project_id})
        assert traces.status_code == 401, traces.text
    finally:
        doomed.client.close()
        _soft_delete(doomed.org_id)


def test_a_profile_ingested_with_one_organizations_key_never_surfaces_under_another(
    newcomer: LiveTenant, seeded: LiveTenant, ingested_profile: None
) -> None:
    """F17 AC7: the seeded tenant cannot name the newcomer's project, and its own
    profiled services, graph and filters never hold the newcomer's profile."""
    for path, params in (
        ("/profiles/services", {}),
        ("/profiles/graph", {"service_name": PROFILE_SERVICE, "type": "cpu"}),
        ("/profiles/filters", {"service_name": PROFILE_SERVICE, "type": "cpu"}),
        ("/profiles/compare", {"service_name": PROFILE_SERVICE, "type": "cpu", "version": "x"}),
    ):
        foreign = seeded.client.get(path, params={"project_id": newcomer.project_id, **params})
        assert foreign.status_code == 404, f"{path}: {foreign.text}"

    own = _profiled_services(seeded, seeded.project_id)
    assert own.status_code == 200, own.text
    assert PROFILE_SERVICE not in {s["service_name"] for s in own.json()["services"]}

    graph = seeded.client.get(
        "/profiles/graph",
        params={"project_id": seeded.project_id, "service_name": PROFILE_SERVICE, "type": "cpu"},
    )
    assert graph.status_code == 200, graph.text
    assert graph.json()["samples"] == 0

    mine = newcomer.client.get(
        "/profiles/graph",
        params={"project_id": newcomer.project_id, "service_name": PROFILE_SERVICE, "type": "cpu"},
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["functions"][0]["name"] == "isolation.probe"
