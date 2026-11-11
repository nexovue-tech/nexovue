"""The gate's canary (F3 plan T5.6; AC49, with AC59's search of what a run leaves).

A unique marker is planted in every field an attacker can reach: the service and endpoint,
the finding's title, a version, an exception's type and message, span and log attributes, a
log body, a deploy's metadata, a callee's name, an alert rule's name, a check's name and the
project's name. Then the real chain runs: `gather` reads them from Postgres and ClickHouse,
decides the wording, and `word`, as `nexovue_insight`, sends the request to a fake provider
that records every byte. **No marker may appear in those bytes** (BR8).

The test proves it can see: the provider received a request; each marker the record keeps as
a name is in `aliases` or `evidence`, so it was read and turned into a stand-in; and a control
marker planted directly in `model_input` is caught. It runs on every commit in `just check`
and needs no model.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Final

import clickhouse_connect
import pytest
from alert_fixtures import AlertTenant, add_finding, postgres_reachable
from clickhouse_cleanup import delete_after_run
from detection_history import CLICKHOUSE_DSN, READER_DSN
from live import require_stack
from recording_site import RecordingSite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import CHAT_PATH, NOW, answer, connect, openai_reply, run_word

from nexovue_scheduler.evidence.gather import gather
from nexovue_scheduler.evidence.store import connect_store

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_insight_canary.py",
    remedy="Postgres and ClickHouse are not reachable — run `just up`",
)

START: Final = NOW - timedelta(minutes=6)
# Names the record keeps, as names: each must reach `aliases` or `evidence`, and no further.
SERVICE: Final = "canary-service-a41"
CALLEE: Final = "canary-callee-b52"
VERSION: Final = "canary-version-c63"
EXCEPTION_TYPE: Final = "CanaryTypeD74"
CHECK_NAME: Final = "canary-check-e85"
# An alert rule's name is customer text the page shows as a name (design, *Reads*).
RULE_NAME: Final = "canary-rule-m63"
# Text the record never keeps: each must reach no stored row and no byte sent.
NEVER_KEPT: Final = {
    "endpoint": "canary-endpoint-f96",
    "title": "canary-title-g07",
    "message": "canary-message-h18",
    "span attribute": "canary-span-attr-i29",
    "log attribute": "canary-log-attr-j30",
    "log body": "canary-log-body-k41",
    "deploy metadata": "canary-metadata-l52",
    "project name": "canary-project-n74",
}
CONTROL: Final = "canary-control-o85"
NAMES: Final = {
    "service": SERVICE,
    "callee": CALLEE,
    "version": VERSION,
    "exception type": EXCEPTION_TYPE,
    "check": CHECK_NAME,
    "rule name": RULE_NAME,
}


def _at(minutes: float) -> Any:
    return (START + timedelta(minutes=minutes)).replace(tzinfo=None)


def _plant_telemetry(tenant: AlertTenant) -> None:
    for table in ("service_edges_1m", "spans", "logs"):
        delete_after_run(table, str(tenant.org_id))
    client = clickhouse_connect.get_client(dsn=CLICKHOUSE_DSN)
    bound = {"org": str(tenant.org_id), "project": str(tenant.project_id), "service": SERVICE}
    for minute, errors in ((-3, 0), (1, 40)):
        client.command(
            "INSERT INTO service_edges_1m (tenant_id, project_id, client_service, "
            "server_service, server_peer_kind, server_peer_attr, door, edge_kind, minute, "
            "call_count, error_count, latency_quantiles) VALUES (%(org)s, %(project)s, "
            "%(service)s, %(callee)s, 'postgresql', 'peer.service', 'server', 'call', "
            "%(minute)s, 100, %(errors)s, "
            "arrayReduce('quantilesState(0.5, 0.95, 0.99)', [toFloat64(5)]))",
            parameters=bound | {"callee": CALLEE, "minute": _at(minute), "errors": errors},
        )
    client.command(
        "INSERT INTO spans (tenant_id, project_id, service_name, span_name, timestamp, "
        "received_at, exception_type, exception_message, span_attrs, trace_id, span_id) "
        "VALUES (%(org)s, %(project)s, %(service)s, %(endpoint)s, %(at)s, %(at)s, %(type)s, "
        "%(message)s, %(attrs)s, %(trace)s, %(span)s)",
        parameters=bound
        | {
            "endpoint": NEVER_KEPT["endpoint"],
            "at": _at(1),
            "type": EXCEPTION_TYPE,
            "message": NEVER_KEPT["message"],
            "attrs": json.dumps({"note": NEVER_KEPT["span attribute"]}),
            "trace": uuid.uuid4().hex,
            "span": uuid.uuid4().hex[:16],
        },
    )
    client.command(
        "INSERT INTO logs (tenant_id, project_id, service_name, timestamp, received_at, "
        "severity, body, log_attrs, exception_type) VALUES (%(org)s, %(project)s, %(service)s, "
        "%(at)s, %(at)s, 'ERROR', %(body)s, %(attrs)s, %(type)s)",
        parameters=bound
        | {
            "at": _at(1),
            "body": NEVER_KEPT["log body"],
            "attrs": json.dumps({"note": NEVER_KEPT["log attribute"]}),
            "type": EXCEPTION_TYPE,
        },
    )
    client.close()


async def _plant_records(engine: AsyncEngine, tenant: AlertTenant) -> uuid.UUID:
    async with engine.begin() as connection:
        bound = {"org": tenant.org_id, "project": tenant.project_id}
        await connection.execute(
            text("UPDATE projects SET name = :name WHERE id = :project AND org_id = :org"),
            bound | {"name": NEVER_KEPT["project name"]},
        )
        release = await connection.scalar(
            text(
                "INSERT INTO releases (org_id, project_id, service_name, version, "
                "first_reported_at, last_reported_at, report_count, last_state, last_activity_at) "
                "VALUES (:org, :project, :service, :version, :first, :at, 1, 'finished', now()) "
                "RETURNING id"
            ),
            bound
            | {
                "service": SERVICE,
                "version": VERSION,
                "first": START - timedelta(days=20),
                "at": START - timedelta(minutes=4),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO deploy_events (org_id, project_id, version, service_name, "
                "deployed_at, source, delivery_id, metadata, state, release_id) VALUES (:org, "
                ":project, :version, :service, :at, 'ci', :delivery, CAST(:metadata AS jsonb), "
                "'finished', :release)"
            ),
            bound
            | {
                "version": VERSION,
                "service": SERVICE,
                "at": START - timedelta(minutes=4),
                "delivery": str(uuid.uuid4()),
                "metadata": json.dumps({"note": NEVER_KEPT["deploy metadata"]}),
                "release": release,
            },
        )
        check_id = uuid.uuid4()
        await connection.execute(
            text(
                "INSERT INTO checks (id, org_id, project_id, name, type, config, "
                "interval_seconds, enabled) VALUES (:id, :org, :project, :name, 'tcp', "
                '\'{"type": "tcp", "host": "example.test", "port": 443}\', 60, false)'
            ),
            bound | {"id": check_id, "name": CHECK_NAME},
        )
        await connection.execute(
            text(
                "INSERT INTO check_state_changes (org_id, project_id, check_id, from_state, "
                "to_state, changed_at, reason) VALUES (:org, :project, :check, 'up', 'down', "
                ":at, 'x')"
            ),
            bound | {"check": check_id, "at": START + timedelta(minutes=1)},
        )
        finding_id: uuid.UUID = await add_finding(
            connection, tenant, service=SERVICE, started_at=START
        )
        await connection.execute(
            text(
                "UPDATE findings SET title = :title, signals = CAST(:signals AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "id": finding_id,
                "title": NEVER_KEPT["title"],
                "signals": json.dumps(
                    [
                        {
                            "signal": "slower",
                            "key": f"endpoint:{NEVER_KEPT['endpoint']}",
                            "endpoint": NEVER_KEPT["endpoint"],
                            "normal": 110.0,
                            "worst": 412.0,
                        }
                    ]
                ),
            },
        )
        incident = await connection.scalar(
            text(
                "INSERT INTO incidents (org_id, project_id, rule_name, condition_kind, "
                "subject_key, subject_label, severity, opened_at) VALUES (:org, :project, :rule, "
                "'service_latency_p95', :service, :service, 'warning', :at) RETURNING id"
            ),
            bound
            | {
                "rule": RULE_NAME,
                "service": SERVICE,
                "at": START + timedelta(minutes=1),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO finding_incidents (org_id, finding_id, incident_id) "
                "VALUES (:org, :finding, :incident)"
            ),
            {"org": tenant.org_id, "finding": finding_id, "incident": incident},
        )
    return finding_id


async def _stored(engine: AsyncEngine, finding_id: uuid.UUID) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT id, evidence, aliases, model_input, wording_state "
                    "FROM finding_explanations WHERE finding_id = :id ORDER BY version DESC"
                ),
                {"id": finding_id},
            )
        ).first()


@pytest.fixture
async def planted(
    ai_install: AsyncEngine, ai_tenant: AlertTenant, provider_site: RecordingSite
) -> tuple[Any, Any]:
    connected = await connect(ai_install, ai_tenant, provider_site)
    _plant_telemetry(ai_tenant)
    finding_id = await _plant_records(ai_install, ai_tenant)
    store = connect_store(READER_DSN)
    await gather(
        ai_install,
        store,
        finding_id=finding_id,
        org_id=ai_tenant.org_id,
        reason="opened",
        deployment="self-hosted",
        now=NOW,
        show_held_causes=True,
    )
    return connected, await _stored(ai_install, finding_id)


async def test_no_planted_marker_reaches_the_bytes_sent_to_the_provider(
    planted: tuple[Any, Any], ai_engine: AsyncEngine, provider_site: RecordingSite
) -> None:
    """AC49: the chain ran, the names were read, and nothing an attacker wrote went out."""
    connected, row = planted
    provider_site.replies[CHAT_PATH] = openai_reply(answer())
    assert row.wording_state == "pending"

    await run_word(ai_engine, connected, str(row.id))

    [sent] = provider_site.requests
    outgoing = sent.body.decode() + json.dumps(sent.headers) + sent.path
    record = json.dumps([row.evidence, row.aliases])
    for what, marker in NAMES.items():
        assert marker not in outgoing, f"the {what} reached the provider"
        assert marker in record, f"the {what} was never read, so this test proves nothing"
    for what, marker in NEVER_KEPT.items():
        assert marker not in outgoing, f"the {what} reached the provider"
        assert marker not in json.dumps([row.evidence, row.aliases, row.model_input]), what
    kinds = {item["kind"] for item in row.evidence["items"]}
    assert {"deploy", "dependency", "error_group", "check", "incident"} <= kinds


async def test_a_control_marker_planted_in_the_model_input_is_caught(
    planted: tuple[Any, Any],
    ai_install: AsyncEngine,
    ai_engine: AsyncEngine,
    provider_site: RecordingSite,
) -> None:
    """AC49's control: the search sees a marker that does reach the bytes, so its silence
    above means something."""
    connected, row = planted
    provider_site.replies[CHAT_PATH] = openai_reply(answer())
    model_input = dict(row.model_input)
    model_input["finding"] = dict(model_input["finding"], unit=CONTROL)
    async with ai_install.begin() as connection:
        await connection.execute(
            text(
                "UPDATE finding_explanations SET model_input = CAST(:input AS jsonb) WHERE id = :id"
            ),
            {"id": row.id, "input": json.dumps(model_input)},
        )

    await run_word(ai_engine, connected, str(row.id))

    [sent] = provider_site.requests
    assert CONTROL in sent.body.decode()
