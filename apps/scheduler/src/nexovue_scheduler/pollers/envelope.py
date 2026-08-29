"""Turn what a poller read into the envelope every door produces.

Door 4b meets the same invariant door 2 does: one internal envelope before the
broker, so a poll needs no table, no topic and no worker of its own. The
wrapping is `publish.wrap_envelope`, shared rather than repeated — a second copy
of that function would be a second definition of the envelope, which is how
doors drift apart.

Names, units, attribute keys and resource keys below are read from
`packages/contracts/fixtures/door4b-cloudwatch.bin` and `door4b-dbstats.bin`.
Where this file and those disagree, the fixtures are right.
"""

from __future__ import annotations

from typing import Final

from nexovue.v1 import envelope_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2
from opentelemetry.proto.resource.v1 import resource_pb2

from nexovue_scheduler.pollers.cloudwatch import ATTR_DB_INSTANCE, RdsReading
from nexovue_scheduler.pollers.postgres_stats import (
    DB_SYSTEM_POSTGRESQL,
    ConnectionCount,
    DatabaseSnapshot,
    StatementStats,
)
from nexovue_scheduler.pollers.window import PollWindow
from nexovue_scheduler.publish import (
    OTLP_PROTO_VERSION,
    SCHEMA_URL,
    wrap_envelope,
)

POLLER_SERVICE_NAME: Final = "nexovue-poller"
POLLER_VERSION: Final = "0.1.0"


SCOPE_CLOUDWATCH: Final = "nexovue.scheduler.pollers.cloudwatch"
SCOPE_POSTGRES: Final = "nexovue.scheduler.pollers.postgres"

METRIC_OPERATION_DURATION: Final = "db.client.operation.duration"
METRIC_CONNECTION_COUNT: Final = "db.client.connection.count"

DESCRIPTION_OPERATION_DURATION: Final = "Mean execution time per normalized statement"
DESCRIPTION_CONNECTION_COUNT: Final = "Backend connections by state"

UNIT_SECONDS: Final = "s"
UNIT_CONNECTIONS: Final = "{connection}"

ATTR_QUERY_HASH: Final = "db.query.hash"
ATTR_DB_SYSTEM: Final = "db.system.name"
ATTR_DB_NAMESPACE: Final = "db.namespace"
ATTR_OPERATION_CALLS: Final = "db.client.operation.calls"
ATTR_OPERATION_MEAN_TIME: Final = "db.client.operation.mean_time"
ATTR_CONNECTION_STATE: Final = "db.client.connection.state"

ATTR_SERVER_ADDRESS: Final = "server.address"
ATTR_SERVER_PORT: Final = "server.port"


def _string(key: str, value: str) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(string_value=value))


def _int(key: str, value: int) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(int_value=value))


def _double(key: str, value: float) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(double_value=value))


def _poller_identity() -> list[common_pb2.KeyValue]:
    """Who took the reading. Identical for every poller, so it is written once."""
    return [
        _string("service.name", POLLER_SERVICE_NAME),
        _string("service.version", POLLER_VERSION),
    ]


def _telemetry_sdk() -> list[common_pb2.KeyValue]:
    """What encoded the payload.

    We build OTLP directly rather than through the OpenTelemetry SDK, so the
    honest version to report is the proto library's — read, not remembered.
    """
    return [
        _string("telemetry.sdk.name", "opentelemetry"),
        _string("telemetry.sdk.language", "python"),
        _string("telemetry.sdk.version", OTLP_PROTO_VERSION),
    ]


def cloudwatch_resource(*, environment: str, region: str, account_id: str) -> resource_pb2.Resource:
    """Which AWS account and region a reading came from.

    `cloud.account.id` is load-bearing rather than decoration: one customer can
    connect several accounts, and two instances in different accounts may share
    an identifier.
    """
    resource = resource_pb2.Resource()
    resource.attributes.extend(
        [
            *_poller_identity(),
            _string("deployment.environment.name", environment),
            *_telemetry_sdk(),
            _string("cloud.provider", "aws"),
            _string("cloud.region", region),
            _string("cloud.account.id", account_id),
        ]
    )
    return resource


def postgres_resource(
    *, environment: str, server_address: str, server_port: int
) -> resource_pb2.Resource:
    """Which database server a reading came from. Address and port, never a DSN."""
    resource = resource_pb2.Resource()
    resource.attributes.extend(
        [
            *_poller_identity(),
            _string("deployment.environment.name", environment),
            *_telemetry_sdk(),
            _string(ATTR_SERVER_ADDRESS, server_address),
            _int(ATTR_SERVER_PORT, server_port),
        ]
    )
    return resource


def build_cloudwatch_envelope(
    readings: list[RdsReading],
    *,
    org_id: str,
    project_id: str,
    environment: str,
    region: str,
    account_id: str,
    window: PollWindow,
) -> bytes:
    """Encode one CloudWatch poll as a serialized `nexovue.v1.Envelope`."""
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add(schema_url=SCHEMA_URL)
    resource_metrics.resource.CopyFrom(
        cloudwatch_resource(environment=environment, region=region, account_id=account_id)
    )
    scope_metrics = _scope(resource_metrics, SCOPE_CLOUDWATCH)
    _add_rds_metrics(scope_metrics, readings, window)

    return _wrap(request, org_id=org_id, project_id=project_id, window=window)


def build_postgres_envelope(
    snapshot: DatabaseSnapshot,
    *,
    org_id: str,
    project_id: str,
    environment: str,
    server_address: str,
    server_port: int,
    window: PollWindow,
) -> bytes:
    """Encode one database poll as a serialized `nexovue.v1.Envelope`."""
    request = metrics_service_pb2.ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add(schema_url=SCHEMA_URL)
    resource_metrics.resource.CopyFrom(
        postgres_resource(
            environment=environment, server_address=server_address, server_port=server_port
        )
    )
    scope_metrics = _scope(resource_metrics, SCOPE_POSTGRES)
    _add_statement_metric(scope_metrics, snapshot.statements, window)
    _add_connection_metric(scope_metrics, snapshot.connections, window)

    return _wrap(request, org_id=org_id, project_id=project_id, window=window)


def _scope(resource_metrics: metrics_pb2.ResourceMetrics, name: str) -> metrics_pb2.ScopeMetrics:
    scope_metrics = resource_metrics.scope_metrics.add(schema_url=SCHEMA_URL)
    scope_metrics.scope.name = name
    scope_metrics.scope.version = POLLER_VERSION
    return scope_metrics


def _wrap(
    request: metrics_service_pb2.ExportMetricsServiceRequest,
    *,
    org_id: str,
    project_id: str,
    window: PollWindow,
) -> bytes:
    return wrap_envelope(
        request.SerializeToString(),
        door=envelope_pb2.DOOR_POLLER,
        signal=envelope_pb2.SIGNAL_METRICS,
        org_id=org_id,
        project_id=project_id,
        # The end of the window, which is when this poll observed the system.
        observed_at_unix_nano=window.end_unix_nano,
    )


def _add_rds_metrics(
    scope_metrics: metrics_pb2.ScopeMetrics,
    readings: list[RdsReading],
    window: PollWindow,
) -> None:
    """One metric per distinct name, with a point per instance.

    Gauges, not sums. CloudWatch already averaged over the period, so the value
    describes a level rather than a quantity accumulated since the last reading;
    a sum here would make a rate out of something that has none.

    Readings are grouped rather than appended blindly, because OTLP requires one
    `Metric` per name within a scope — repeating the name would produce two
    series that storage has to reconcile.
    """
    by_name: dict[str, metrics_pb2.Metric] = {}
    for reading in readings:
        metric = by_name.get(reading.metric.otel_name)
        if metric is None:
            metric = scope_metrics.metrics.add(
                name=reading.metric.otel_name, unit=reading.metric.unit
            )
            by_name[reading.metric.otel_name] = metric

        point = metric.gauge.data_points.add(
            start_time_unix_nano=window.start_unix_nano,
            time_unix_nano=reading.at_unix_nano,
        )
        if reading.metric.as_integer:
            point.as_int = int(reading.value)
        else:
            point.as_double = reading.value
        point.attributes.append(_string(ATTR_DB_INSTANCE, reading.instance_identifier))


def _add_statement_metric(
    scope_metrics: metrics_pb2.ScopeMetrics,
    statements: list[StatementStats],
    window: PollWindow,
) -> None:
    """Mean time per normalized statement, one point per statement.

    `calls` rides along as an attribute rather than as its own metric because it
    is what makes the mean readable: 300ms across four calls is noise, and
    300ms across ninety thousand is the incident.
    """
    if not statements:
        return
    metric = scope_metrics.metrics.add(
        name=METRIC_OPERATION_DURATION,
        description=DESCRIPTION_OPERATION_DURATION,
        unit=UNIT_SECONDS,
    )
    for statement in statements:
        point = metric.gauge.data_points.add(
            start_time_unix_nano=window.start_unix_nano,
            time_unix_nano=window.end_unix_nano,
            as_double=statement.mean_seconds,
        )
        point.attributes.extend(
            [
                _string(ATTR_QUERY_HASH, statement.query_hash),
                _string(ATTR_DB_SYSTEM, DB_SYSTEM_POSTGRESQL),
                _string(ATTR_DB_NAMESPACE, statement.namespace),
                _int(ATTR_OPERATION_CALLS, statement.calls),
                _double(ATTR_OPERATION_MEAN_TIME, statement.mean_seconds),
            ]
        )


def _add_connection_metric(
    scope_metrics: metrics_pb2.ScopeMetrics,
    connections: list[ConnectionCount],
    window: PollWindow,
) -> None:
    """Backends by state: how close this database is to refusing connections."""
    if not connections:
        return
    metric = scope_metrics.metrics.add(
        name=METRIC_CONNECTION_COUNT,
        description=DESCRIPTION_CONNECTION_COUNT,
        unit=UNIT_CONNECTIONS,
    )
    for connection in connections:
        point = metric.gauge.data_points.add(
            start_time_unix_nano=window.start_unix_nano,
            time_unix_nano=window.end_unix_nano,
            as_int=connection.backends,
        )
        point.attributes.append(_string(ATTR_CONNECTION_STATE, connection.state))
