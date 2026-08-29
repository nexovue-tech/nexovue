"""Reading door 4b connectors out of Postgres, and parsing what they configure.

Same arrangement as `checks.py`: the `integrations` table is owned by the API,
this service only reads it, and it reads by column name rather than by importing
the API's models — the two deploy separately, and a scheduler that cannot start
because a control-plane package moved is a scheduler running nobody's polls.

Nothing here is a secret. Under ADR 0076 an integration stores a role ARN and an
ExternalId, both public values, and the credential that actually opens the door
is minted per poll and thrown away. The column is still called
`config_encrypted` because it was named before that decision; it holds plain
UTF-8 JSON, and the name is now misleading rather than wrong-in-effect.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.pollers.aws import RoleGrant
from nexovue_scheduler.pollers.postgres_connect import AUTH_RDS_IAM, DEFAULT_PORT, DatabaseTarget

KIND_CLOUDWATCH: Final = "cloudwatch"
KIND_POSTGRES: Final = "postgres"

STATUS_ACTIVE: Final = "active"

# How often a connector is polled when its config does not say. CloudWatch
# publishes standard RDS metrics once a minute, so polling faster costs money
# and returns the same number twice.
DEFAULT_INTERVAL_SECONDS: Final = 60
CONFIG_INTERVAL_SECONDS: Final = "interval_seconds"

_ACTIVE_INTEGRATIONS: Final = text(
    """
    SELECT id, org_id, project_id, kind, config_encrypted
    FROM integrations
    WHERE status = :status
    ORDER BY id
    """
)


class IntegrationConfigError(ValueError):
    """A stored connector that cannot be turned into a poll."""


@dataclass(frozen=True, slots=True)
class IntegrationDefinition:
    """One row of `integrations`, as the scheduler sees it.

    Carries `org_id` and `project_id` because the poll that runs from it has to
    stamp them on the envelope — a reading that reaches the broker without a
    tenant is a reading nothing can attribute or isolate.
    """

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    config: dict[str, Any]

    @property
    def schedule_name(self) -> str:
        """The name this connector goes by in the beat schedule.

        The row id, not the kind: one project may connect two AWS accounts, and
        a name collision would silently drop one of them.
        """
        return f"integration:{self.id}"

    @property
    def interval_seconds(self) -> int:
        requested = self.config.get(CONFIG_INTERVAL_SECONDS, DEFAULT_INTERVAL_SECONDS)
        try:
            seconds = int(requested)
        except (TypeError, ValueError):
            seconds = DEFAULT_INTERVAL_SECONDS
        return max(1, seconds)


async def load_active_integrations(engine: AsyncEngine) -> list[IntegrationDefinition]:
    """Every active connector, for one refresh of the beat schedule.

    A row whose config will not parse is skipped rather than raising. One
    customer's broken connector must not stop every other customer's polls from
    being scheduled.
    """
    async with engine.connect() as connection:
        rows = (
            (await connection.execute(_ACTIVE_INTEGRATIONS, {"status": STATUS_ACTIVE}))
            .mappings()
            .all()
        )

    definitions = []
    for row in rows:
        try:
            config = _decode_config(row["config_encrypted"])
        except IntegrationConfigError:
            continue
        definitions.append(
            IntegrationDefinition(
                id=row["id"],
                org_id=row["org_id"],
                project_id=row["project_id"],
                kind=row["kind"],
                config=config,
            )
        )
    return definitions


def _decode_config(stored: bytes | None) -> dict[str, Any]:
    if not stored:
        raise IntegrationConfigError("integration has no configuration")
    try:
        decoded = json.loads(bytes(stored).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrationConfigError(f"configuration is not JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise IntegrationConfigError("configuration is not a JSON object")
    return decoded


def role_grant_from(config: dict[str, Any]) -> RoleGrant:
    """The two public values that let us into a customer's AWS account."""
    role_arn = config.get("role_arn")
    external_id = config.get("external_id")
    if not isinstance(role_arn, str) or not isinstance(external_id, str):
        raise IntegrationConfigError(
            "an AWS integration needs a string `role_arn` and a string `external_id`"
        )
    return RoleGrant(role_arn=role_arn, external_id=external_id)


def rds_instances_from(config: dict[str, Any]) -> list[str]:
    """Which RDS instances to read. Named explicitly rather than discovered:
    a poller that expands to every instance in an account bills the customer for
    metrics they never asked us to watch."""
    identifiers = config.get("db_instance_identifiers")
    if not isinstance(identifiers, list) or not all(isinstance(one, str) for one in identifiers):
        raise IntegrationConfigError(
            "a CloudWatch integration needs `db_instance_identifiers` as a list of strings"
        )
    return [str(one) for one in identifiers]


def database_target_from(config: dict[str, Any]) -> DatabaseTarget:
    """Which database to read and how we may authenticate to it."""
    host = config.get("host")
    database = config.get("database")
    user = config.get("user")
    if not isinstance(host, str) or not isinstance(database, str) or not isinstance(user, str):
        raise IntegrationConfigError(
            "a Postgres integration needs string `host`, `database` and `user`"
        )

    auth = str(config.get("auth", AUTH_RDS_IAM))
    grant = role_grant_from(config) if auth == AUTH_RDS_IAM else None
    return DatabaseTarget(
        host=host,
        port=int(config.get("port", DEFAULT_PORT)),
        database=database,
        user=user,
        auth=auth,
        grant=grant,
    )
