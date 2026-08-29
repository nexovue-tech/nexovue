"""Reading RDS metrics out of a customer's CloudWatch.

Deliberately four metrics, not forty. CloudWatch bills per metric read, and a
poller that pulls everything AWS publishes produces a dashboard nobody can read
and an invoice nobody expected. These four are the ones that explain a slow
database: it is busy, it is out of connections, it is out of disk, or its disks
are slow. The set is the one the frozen fixture froze
(`packages/contracts/fixtures/door4b-cloudwatch.bin`).

`GetMetricData` rather than `GetMetricStatistics`: one request covers every
metric for every instance, where the older call needs one request each.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from nexovue_scheduler.pollers.window import PollWindow, to_unix_nano

NAMESPACE_RDS: Final = "AWS/RDS"
DIMENSION_DB_INSTANCE: Final = "DBInstanceIdentifier"
ATTR_DB_INSTANCE: Final = "aws.rds.instance.identifier"

# CloudWatch's finest resolution for standard RDS metrics. Asking for less than
# it publishes returns empty periods that look like an outage.
PERIOD_SECONDS: Final = 60

# One request returns at most this many instance/metric pairs. Beyond it AWS
# paginates, and a silently truncated page is a set of instances that stop
# appearing on the graph with no error anywhere.
MAX_QUERIES_PER_REQUEST: Final = 500


@dataclass(frozen=True, slots=True)
class RdsMetric:
    """One CloudWatch metric and the OTLP name it normalizes to.

    `as_integer` is not cosmetic: a connection count or a byte count is a whole
    thing, and reporting 184.0 connections invites a reader to wonder what the
    fraction means.
    """

    cloudwatch_name: str
    otel_name: str
    unit: str
    as_integer: bool


# Units are OTLP's (UCUM), not CloudWatch's: "By" not "Bytes", "%" not "Percent".
RDS_METRICS: Final[tuple[RdsMetric, ...]] = (
    RdsMetric("CPUUtilization", "aws.rds.cpu.utilization", "%", as_integer=False),
    RdsMetric("DatabaseConnections", "aws.rds.database.connections", "{connection}", True),
    RdsMetric("FreeStorageSpace", "aws.rds.storage.free", "By", as_integer=True),
    RdsMetric("ReadLatency", "aws.rds.read.latency", "s", as_integer=False),
)


@dataclass(frozen=True, slots=True)
class RdsReading:
    """One value CloudWatch returned, for one metric on one instance."""

    metric: RdsMetric
    instance_identifier: str
    value: float
    at_unix_nano: int


class CloudWatchPollError(RuntimeError):
    """The poll cannot be trusted, so it must not be published as if it were."""


def collect_rds_readings(
    cloudwatch_client: Any,
    *,
    instance_identifiers: list[str],
    window: PollWindow,
) -> list[RdsReading]:
    """The most recent value of each metric on each instance, over the window.

    Only the latest point per series is kept. A poll runs on the same cadence as
    the period, so the earlier points in the window were already published by
    the previous poll; republishing them would double every series.
    """
    if not instance_identifiers:
        raise CloudWatchPollError("no RDS instances configured for this integration")

    queries, query_subjects = _build_queries(instance_identifiers)
    response = cloudwatch_client.get_metric_data(
        MetricDataQueries=queries,
        StartTime=window.start,
        EndTime=window.end,
        # Newest first, so the point we want is at index 0 rather than found.
        ScanBy="TimestampDescending",
    )

    readings = []
    for series in response["MetricDataResults"]:
        subject = query_subjects.get(series["Id"])
        if subject is None or not series["Values"]:
            # No datapoint in the window is normal — a stopped instance, or a
            # metric AWS has not published yet. Silence is not a zero.
            continue
        metric, instance_identifier = subject
        readings.append(
            RdsReading(
                metric=metric,
                instance_identifier=instance_identifier,
                value=float(series["Values"][0]),
                at_unix_nano=to_unix_nano(series["Timestamps"][0]),
            )
        )
    return readings


def _build_queries(
    instance_identifiers: list[str],
) -> tuple[list[dict[str, Any]], dict[str, tuple[RdsMetric, str]]]:
    """One query per metric per instance, plus the map back to what each asked for.

    CloudWatch answers with the opaque ids it was given and nothing else, so the
    mapping has to be kept here or the values arrive unattributable.
    """
    queries: list[dict[str, Any]] = []
    subjects: dict[str, tuple[RdsMetric, str]] = {}

    for instance_index, instance_identifier in enumerate(instance_identifiers):
        for metric_index, metric in enumerate(RDS_METRICS):
            # Ids must start with a lowercase letter and hold only word
            # characters, so they are generated rather than derived from names.
            query_id = f"q{instance_index}_{metric_index}"
            subjects[query_id] = (metric, instance_identifier)
            queries.append(
                {
                    "Id": query_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": NAMESPACE_RDS,
                            "MetricName": metric.cloudwatch_name,
                            "Dimensions": [
                                {"Name": DIMENSION_DB_INSTANCE, "Value": instance_identifier}
                            ],
                        },
                        "Period": PERIOD_SECONDS,
                        "Stat": "Average",
                    },
                }
            )

    if len(queries) > MAX_QUERIES_PER_REQUEST:
        raise CloudWatchPollError(
            f"{len(queries)} metric queries exceeds CloudWatch's limit of "
            f"{MAX_QUERIES_PER_REQUEST}; split this integration across projects"
        )
    return queries, subjects
