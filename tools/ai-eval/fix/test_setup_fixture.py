"""AC37c's setup fixture: 30 services in two environments, and where their code lives (F5 plan
T9.4). The timing itself is a person's, the founder's to arrange; this proves the fixture is
the shape the spec names."""

from __future__ import annotations

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from fix import setup_fixture


def _resources(body: bytes) -> list[dict[str, str]]:
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    return [
        {each.key: each.value.string_value for each in spans.resource.attributes}
        for spans in request.resource_spans
    ]


def test_ac37c_thirty_services_in_two_environments() -> None:
    resources = _resources(setup_fixture.export_request(at_ns=1_700_000_000_000_000_000))

    pairs = {(each["service.name"], each["deployment.environment.name"]) for each in resources}
    assert len({service for service, _ in pairs}) == 30
    assert {environment for _, environment in pairs} == {"production", "staging"}
    assert len(pairs) == 60


def test_every_service_has_one_code_location_across_a_few_repositories() -> None:
    locations = setup_fixture.code_locations()

    assert sorted(locations) == sorted(setup_fixture.SERVICES)
    repositories = {repository for repository, _ in locations.values()}
    assert 2 <= len(repositories) <= 5
    assert len(set(locations.values())) == 30


@pytest.mark.parametrize("host", ["ingest.example.com", "10.0.0.5"])
def test_it_refuses_any_receiver_but_this_machine(host: str) -> None:
    with pytest.raises(SystemExit):
        setup_fixture.require_local(f"http://{host}:4318")
