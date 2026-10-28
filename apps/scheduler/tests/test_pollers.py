"""Door 4b produces the envelopes the frozen contract describes (P1-37).

Every expectation here is read out of
`packages/contracts/fixtures/door4b-cloudwatch.bin` and `door4b-dbstats.bin`
rather than written down a second time. Hardcoding the metric names would let
this file and the contract drift apart while both looked correct; deriving them
means a change to the contract fails this test, which is the point of freezing
it.

AWS is never called. `botocore.stub.Stubber` answers STS and CloudWatch from the
service model, so the request parameters are validated against AWS's own shapes
without a credential or a network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import boto3
import pytest
from botocore.stub import Stubber
from nexovue.v1 import envelope_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2

from nexovue_scheduler.pollers import cloudwatch
from nexovue_scheduler.pollers.aws import (
    SESSION_DURATION_SECONDS,
    RoleGrant,
    RoleGrantError,
    assume_customer_role,
    session_name_for,
)
from nexovue_scheduler.pollers.envelope import (
    build_cloudwatch_envelope,
    build_postgres_envelope,
)
from nexovue_scheduler.pollers.postgres_stats import (
    ConnectionCount,
    DatabaseSnapshot,
    StatementStats,
    connection_counts_from_rows,
    statement_stats_from_rows,
)
from nexovue_scheduler.pollers.window import PollWindow
from nexovue_scheduler.publish import SEMCONV_VERSION

FIXTURES: Final = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"

ORG_ID: Final = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
PROJECT_ID: Final = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
INTEGRATION_ID: Final = "01996d5e-9a40-7e02-a000-000000000202"

ACCOUNT_ID: Final = "123456789012"
ROLE_ARN: Final = f"arn:aws:iam::{ACCOUNT_ID}:role/NexovueReadOnly"
EXTERNAL_ID: Final = "9f3d1c7a-6b48-4a2e-9d51-0c7e2b4f8a13"
REGION: Final = "eu-west-1"

WINDOW: Final = PollWindow(
    start=datetime(2026, 5, 21, 12, 35, tzinfo=UTC),
    end=datetime(2026, 5, 21, 12, 36, tzinfo=UTC),
)
DATAPOINT_AT: Final = datetime(2026, 5, 21, 12, 36, tzinfo=UTC)


# --- reading the contract ---------------------------------------------------


def _metrics_of(envelope_bytes: bytes) -> metrics_service_pb2.ExportMetricsServiceRequest:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(envelope_bytes)
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    request.ParseFromString(envelope.payload)
    return request


def _metric_signatures(
    request: metrics_service_pb2.ExportMetricsServiceRequest,
) -> set[tuple[str, str, str]]:
    """Name, unit and description — the three things a reader of a graph sees."""
    return {
        (metric.name, metric.unit, metric.description)
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def _resource_keys(request: metrics_service_pb2.ExportMetricsServiceRequest) -> set[str]:
    return {
        attribute.key
        for resource_metrics in request.resource_metrics
        for attribute in resource_metrics.resource.attributes
    }


def _scope_names(request: metrics_service_pb2.ExportMetricsServiceRequest) -> set[str]:
    return {
        scope_metrics.scope.name
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
    }


def _schema_urls(request: metrics_service_pb2.ExportMetricsServiceRequest) -> set[str]:
    urls = set()
    for resource_metrics in request.resource_metrics:
        urls.add(resource_metrics.schema_url)
        for scope_metrics in resource_metrics.scope_metrics:
            urls.add(scope_metrics.schema_url)
    return urls


def _point_attribute_keys(
    request: metrics_service_pb2.ExportMetricsServiceRequest, metric_name: str
) -> set[str]:
    return {
        attribute.key
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        if metric.name == metric_name
        for point in metric.gauge.data_points
        for attribute in point.attributes
    }


def _fixture(name: str) -> metrics_service_pb2.ExportMetricsServiceRequest:
    return _metrics_of((FIXTURES / name).read_bytes())


@pytest.fixture(scope="module")
def cloudwatch_contract() -> metrics_service_pb2.ExportMetricsServiceRequest:
    return _fixture("door4b-cloudwatch.bin")


@pytest.fixture(scope="module")
def dbstats_contract() -> metrics_service_pb2.ExportMetricsServiceRequest:
    return _fixture("door4b-dbstats.bin")


# --- assuming the customer's role -------------------------------------------


def _sts_credentials() -> dict[str, Any]:
    return {
        "Credentials": {
            "AccessKeyId": "ASIAEXAMPLEEXAMPLE00",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "FQoGZXIvYXdzEExampleSessionToken",
            "Expiration": datetime(2026, 5, 21, 12, 51, tzinfo=UTC),
        },
        "AssumedRoleUser": {
            "AssumedRoleId": "AROAEXAMPLEID:nexovue",
            "Arn": f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/NexovueReadOnly/nexovue",
        },
    }


def _expected_assume_role_params() -> dict[str, Any]:
    """What AssumeRole must carry. The ExternalId is the load-bearing entry.

    Without it, anyone who learns another customer's role ARN could ask us to
    poll that account and we would faithfully do it — the confused deputy
    problem AWS describes. Asserting the parameter here is what stops that
    protection being dropped by a refactor that still passes every other test.
    """
    return {
        "RoleArn": ROLE_ARN,
        "RoleSessionName": session_name_for(INTEGRATION_ID),
        "ExternalId": EXTERNAL_ID,
        "DurationSeconds": SESSION_DURATION_SECONDS,
    }


def test_assuming_a_customer_role_sends_the_external_id() -> None:
    """The whole cross-account design rests on this one parameter."""
    sts = boto3.client("sts", region_name=REGION, aws_access_key_id="x", aws_secret_access_key="y")
    with Stubber(sts) as stub:
        stub.add_response("assume_role", _sts_credentials(), _expected_assume_role_params())
        session = assume_customer_role(
            RoleGrant(role_arn=ROLE_ARN, external_id=EXTERNAL_ID),
            session_name=session_name_for(INTEGRATION_ID),
            region=REGION,
            sts_client=sts,
        )
        stub.assert_no_pending_responses()

    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.token, "the session must carry the temporary token, not a long-term key"


def test_a_grant_without_an_external_id_is_refused() -> None:
    """Refused at construction, so no code path can reach AssumeRole without one."""
    with pytest.raises(RoleGrantError):
        RoleGrant(role_arn=ROLE_ARN, external_id="")


def test_the_account_id_is_read_off_the_role_arn() -> None:
    """Stored once. A second config field could disagree with this one, and
    `cloud.account.id` is what separates two instances that share a name."""
    assert RoleGrant(role_arn=ROLE_ARN, external_id=EXTERNAL_ID).account_id == ACCOUNT_ID


def test_something_that_is_not_a_role_arn_is_refused() -> None:
    with pytest.raises(RoleGrantError):
        RoleGrant(role_arn="AKIAEXAMPLEACCESSKEY", external_id=EXTERNAL_ID)


# --- the CloudWatch poll ----------------------------------------------------


def _metric_data_results() -> dict[str, Any]:
    """One value per query, in the order `_build_queries` generates them."""
    values = {
        "q0_0": 71.4,
        "q0_1": 184.0,
        "q0_2": 41875931136.0,
        "q0_3": 0.0043,
    }
    return {
        "MetricDataResults": [
            {
                "Id": query_id,
                "Label": query_id,
                "Timestamps": [DATAPOINT_AT],
                "Values": [value],
                "StatusCode": "Complete",
            }
            for query_id, value in values.items()
        ]
    }


@pytest.fixture
def rds_readings() -> list[cloudwatch.RdsReading]:
    """A real poll, answered by a stub rather than by AWS."""
    client = boto3.client(
        "cloudwatch", region_name=REGION, aws_access_key_id="x", aws_secret_access_key="y"
    )
    with Stubber(client) as stub:
        stub.add_response("get_metric_data", _metric_data_results())
        readings = cloudwatch.collect_rds_readings(
            client, instance_identifiers=["acme-orders-prod"], window=WINDOW
        )
        stub.assert_no_pending_responses()
    return readings


@pytest.fixture
def cloudwatch_envelope(rds_readings: list[cloudwatch.RdsReading]) -> bytes:
    return build_cloudwatch_envelope(
        rds_readings,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        environment="production",
        region=REGION,
        account_id=ACCOUNT_ID,
        window=WINDOW,
    )


def test_the_cloudwatch_request_names_the_instance_and_asks_for_one_minute() -> None:
    """The request itself is validated against AWS's service model.

    A period finer than CloudWatch publishes returns empty buckets that look
    exactly like an instance that stopped reporting.
    """
    queries, subjects = cloudwatch._build_queries(["acme-orders-prod"])

    assert len(queries) == len(cloudwatch.RDS_METRICS)
    assert {query["MetricStat"]["Period"] for query in queries} == {cloudwatch.PERIOD_SECONDS}
    assert {query["MetricStat"]["Metric"]["Namespace"] for query in queries} == {"AWS/RDS"}
    assert {
        dimension["Value"]
        for query in queries
        for dimension in query["MetricStat"]["Metric"]["Dimensions"]
    } == {"acme-orders-prod"}
    assert set(subjects) == {query["Id"] for query in queries}


def test_the_cloudwatch_envelope_declares_the_poller_door_and_the_metrics_signal(
    cloudwatch_envelope: bytes,
) -> None:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(cloudwatch_envelope)

    assert envelope.door == envelope_pb2.DOOR_POLLER
    assert envelope.signal == envelope_pb2.SIGNAL_METRICS
    assert envelope.tenant_id == ORG_ID
    assert envelope.project_id == PROJECT_ID
    assert envelope.semconv_version == SEMCONV_VERSION
    assert envelope.ingest_id, "an envelope with no ingest id cannot be traced through the pipeline"


def test_the_frozen_fixtures_are_this_door(
    cloudwatch_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    """A guard on the fixtures themselves, so a swapped file is caught here."""
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString((FIXTURES / "door4b-cloudwatch.bin").read_bytes())
    assert envelope.door == envelope_pb2.DOOR_POLLER
    assert envelope.signal == envelope_pb2.SIGNAL_METRICS
    assert cloudwatch_contract.resource_metrics


def test_the_cloudwatch_metrics_match_the_contract(
    cloudwatch_envelope: bytes,
    cloudwatch_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    """Exactly the fixture's metric names, units and descriptions — no more."""
    assert _metric_signatures(_metrics_of(cloudwatch_envelope)) == _metric_signatures(
        cloudwatch_contract
    )


def test_the_cloudwatch_resource_says_which_account_and_region(
    cloudwatch_envelope: bytes,
    cloudwatch_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    """One customer can connect several accounts, and two instances in different
    accounts may share an identifier — so the account is part of the reading."""
    assert _resource_keys(_metrics_of(cloudwatch_envelope)) == _resource_keys(cloudwatch_contract)


def test_the_cloudwatch_scope_and_schema_match_the_contract(
    cloudwatch_envelope: bytes,
    cloudwatch_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    built = _metrics_of(cloudwatch_envelope)
    assert _scope_names(built) == _scope_names(cloudwatch_contract)
    assert _schema_urls(built) == _schema_urls(cloudwatch_contract)


def test_every_cloudwatch_point_names_its_instance(
    cloudwatch_envelope: bytes,
    cloudwatch_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    built = _metrics_of(cloudwatch_envelope)
    for name, _, _ in _metric_signatures(cloudwatch_contract):
        assert _point_attribute_keys(built, name) == _point_attribute_keys(
            cloudwatch_contract, name
        )


def test_counts_and_bytes_are_integers_not_floats(cloudwatch_envelope: bytes) -> None:
    """CloudWatch answers in doubles for everything. 184.0 connections invites a
    reader to wonder what the fraction means; there is no fraction."""
    metrics = {
        metric.name: metric
        for resource_metrics in _metrics_of(cloudwatch_envelope).resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }
    connections = metrics["aws.rds.database.connections"].gauge.data_points[0]
    assert connections.WhichOneof("value") == "as_int"
    assert connections.as_int == 184

    storage = metrics["aws.rds.storage.free"].gauge.data_points[0]
    assert storage.WhichOneof("value") == "as_int"

    cpu = metrics["aws.rds.cpu.utilization"].gauge.data_points[0]
    assert cpu.WhichOneof("value") == "as_double"
    assert cpu.as_double == pytest.approx(71.4)


def test_every_point_carries_the_window_it_describes(cloudwatch_envelope: bytes) -> None:
    """A point with only an end time reads as a spot measurement. CloudWatch
    averaged over a period, which is a different claim."""
    points = [
        point
        for resource_metrics in _metrics_of(cloudwatch_envelope).resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        for point in metric.gauge.data_points
    ]
    assert points
    for point in points:
        assert point.start_time_unix_nano == WINDOW.start_unix_nano
        assert point.time_unix_nano > point.start_time_unix_nano


def test_a_series_with_no_datapoint_is_absent_rather_than_zero() -> None:
    """A stopped instance publishes nothing. Reporting zero free storage would
    read as a full disk rather than as a missing reading."""
    client = boto3.client(
        "cloudwatch", region_name=REGION, aws_access_key_id="x", aws_secret_access_key="y"
    )
    empty = {
        "MetricDataResults": [
            {"Id": "q0_0", "Timestamps": [], "Values": [], "StatusCode": "Complete"}
        ]
    }
    with Stubber(client) as stub:
        stub.add_response("get_metric_data", empty)
        readings = cloudwatch.collect_rds_readings(
            client, instance_identifiers=["acme-orders-prod"], window=WINDOW
        )
    assert readings == []


def test_an_integration_with_no_instances_is_an_error_not_an_empty_poll() -> None:
    with pytest.raises(cloudwatch.CloudWatchPollError):
        cloudwatch.collect_rds_readings(object(), instance_identifiers=[], window=WINDOW)


# --- the database poll ------------------------------------------------------


@pytest.fixture
def snapshot() -> DatabaseSnapshot:
    """Rows shaped as `pg_stat_statements` and `pg_stat_activity` return them,
    normalized by the same functions the live poller uses."""
    statements = statement_stats_from_rows(
        [
            {
                "query_hash": "-4213885652027451965",
                "namespace": "shop",
                "calls": 1284,
                "mean_milliseconds": 318.4,
            },
            {
                "query_hash": "8871209934417210012",
                "namespace": "shop",
                "calls": 91402,
                "mean_milliseconds": 2.1,
            },
        ]
    )
    connections = connection_counts_from_rows(
        [{"state": "active", "backends": 41}, {"state": "idle", "backends": 143}]
    )
    return DatabaseSnapshot(statements=statements, connections=connections)


@pytest.fixture
def postgres_envelope(snapshot: DatabaseSnapshot) -> bytes:
    return build_postgres_envelope(
        snapshot,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        environment="production",
        server_address="orders-db.internal",
        server_port=5432,
        window=WINDOW,
    )


def test_postgres_milliseconds_become_otlp_seconds(snapshot: DatabaseSnapshot) -> None:
    """The fixture's unit is `s`. Postgres reports milliseconds, and a
    millisecond value under an `s` label is off by a thousand with nothing on
    the graph to reveal it."""
    assert snapshot.statements[0].mean_seconds == pytest.approx(0.3184)
    assert snapshot.statements[1].mean_seconds == pytest.approx(0.0021)


def test_the_postgres_envelope_declares_the_poller_door(postgres_envelope: bytes) -> None:
    envelope = envelope_pb2.Envelope()
    envelope.ParseFromString(postgres_envelope)
    assert envelope.door == envelope_pb2.DOOR_POLLER
    assert envelope.signal == envelope_pb2.SIGNAL_METRICS


def test_the_database_metrics_match_the_contract(
    postgres_envelope: bytes,
    dbstats_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    assert _metric_signatures(_metrics_of(postgres_envelope)) == _metric_signatures(
        dbstats_contract
    )


def test_the_database_resource_and_scope_match_the_contract(
    postgres_envelope: bytes,
    dbstats_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    built = _metrics_of(postgres_envelope)
    assert _resource_keys(built) == _resource_keys(dbstats_contract)
    assert _scope_names(built) == _scope_names(dbstats_contract)
    assert _schema_urls(built) == _schema_urls(dbstats_contract)


def test_every_database_point_carries_the_contract_s_attributes(
    postgres_envelope: bytes,
    dbstats_contract: metrics_service_pb2.ExportMetricsServiceRequest,
) -> None:
    built = _metrics_of(postgres_envelope)
    for name, _, _ in _metric_signatures(dbstats_contract):
        assert _point_attribute_keys(built, name) == _point_attribute_keys(dbstats_contract, name)


def test_the_statement_text_is_never_published(postgres_envelope: bytes) -> None:
    """`pg_stat_statements` normalizes literals out, but not always: a query
    built by string concatenation carries its parameters into `query`. Those are
    customer data, so only the hash leaves their database."""
    assert b"SELECT" not in postgres_envelope
    assert b"queryid" not in postgres_envelope


def test_an_empty_snapshot_publishes_no_metrics() -> None:
    """A database with no statements recorded yet claims nothing, rather than
    claiming zero calls at zero milliseconds."""
    request = _metrics_of(
        build_postgres_envelope(
            DatabaseSnapshot(statements=[], connections=[]),
            org_id=ORG_ID,
            project_id=PROJECT_ID,
            environment="production",
            server_address="orders-db.internal",
            server_port=5432,
            window=WINDOW,
        )
    )
    assert _metric_signatures(request) == set()


def test_connection_states_survive_normalization() -> None:
    counts = connection_counts_from_rows([{"state": "idle in transaction", "backends": 7}])
    assert counts == [ConnectionCount(state="idle in transaction", backends=7)]


def test_a_statement_row_keeps_its_call_count(snapshot: DatabaseSnapshot) -> None:
    assert snapshot.statements[0] == StatementStats(
        query_hash="-4213885652027451965",
        namespace="shop",
        calls=1284,
        mean_seconds=pytest.approx(0.3184),  # type: ignore[arg-type]
    )


def test_an_aws_client_gives_up_inside_the_task_s_own_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Group 9, C3. botocore's own 60 s outlived the task's 30 s soft limit, so a hung
    STS or CloudWatch call reported 'passed its soft time limit', never the cause.

    Built without the machine's own AWS configuration: a developer signed in with
    `aws login` made this fail on a credential provider the test never asked for."""
    from nexovue_scheduler.pollers.aws import aws_client

    empty = tmp_path / "aws"
    empty.write_text("")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(empty))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(empty))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    for service in ("sts", "cloudwatch"):
        config = aws_client(service, region="eu-west-1").meta.config
        assert config.connect_timeout == 5
        assert config.read_timeout == 10
        assert config.retries["mode"] == "standard"
