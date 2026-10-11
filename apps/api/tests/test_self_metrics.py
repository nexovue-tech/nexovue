"""The API reports its own requests for a monitor to read (ADR-021).

Counted by route group and status class, never by path: a path carries project
and trace ids, and a label taken from one would let any caller grow the series
count without bound.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from nexovue_api.middleware.metrics import RequestMetricsMiddleware
from nexovue_api.settings import API_PREFIX


def _app(registry: CollectorRegistry) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestMetricsMiddleware, registry=registry)
    # Nested as main.py nests them: each feature's router inside the versioned one.
    versioned = APIRouter(prefix=API_PREFIX)
    traces = APIRouter(prefix="/traces")

    @traces.get("/{trace_id}")
    async def trace(trace_id: str) -> dict[str, str]:
        if trace_id == "missing":
            raise HTTPException(status_code=404)
        return {"id": trace_id}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    versioned.include_router(traces)
    app.include_router(versioned)
    return app


def _sample(registry: CollectorRegistry, name: str, **labels: str) -> float | None:
    return registry.get_sample_value(name, labels)


def test_requests_are_counted_by_group_and_status_class_not_by_path() -> None:
    registry = CollectorRegistry()
    client = TestClient(_app(registry))

    client.get(f"{API_PREFIX}/traces/{uuid.uuid4()}")
    client.get(f"{API_PREFIX}/traces/{uuid.uuid4()}")
    client.get(f"{API_PREFIX}/traces/missing")

    total = "nexovue_api_requests_total"
    assert _sample(registry, total, group="traces", status="2xx") == 2
    assert _sample(registry, total, group="traces", status="4xx") == 1
    assert _sample(registry, "nexovue_api_request_seconds_count", group="traces") == 3


def test_a_path_no_route_owns_is_one_group_whatever_it_says() -> None:
    registry = CollectorRegistry()
    client = TestClient(_app(registry))

    for _ in range(3):
        client.get(f"{API_PREFIX}/{uuid.uuid4()}/anything")

    families = {
        sample.labels.get("group") for metric in registry.collect() for sample in metric.samples
    }
    assert families - {None} == {"other"}


def test_the_probes_are_not_counted() -> None:
    registry = CollectorRegistry()
    TestClient(_app(registry)).get("/healthz")

    assert not [s for m in registry.collect() for s in m.samples if s.name.endswith("_total")]
