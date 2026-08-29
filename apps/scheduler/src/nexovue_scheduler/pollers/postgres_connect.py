"""Opening a read-only connection to a customer's Postgres without holding a password.

For RDS this is IAM database authentication: we assume the customer's role, ask
RDS to sign a token for a database user they created, and use that token as the
password. The token is valid for fifteen minutes and we never store it, so there
is no customer database password anywhere in this system.

Self-hosted Postgres has no equivalent, and is **not implemented** — see ADR
0076. Storing a password would need envelope encryption and a key-management
scheme we have not decided on, and inventing one badly is worse than the feature
being absent. Configuring that auth mode fails loudly here rather than falling
back to something weaker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import asyncpg

from nexovue_scheduler.pollers.aws import RoleGrant, assume_customer_role, session_name_for

AUTH_RDS_IAM: Final = "rds_iam"
AUTH_PASSWORD: Final = "password"

DEFAULT_PORT: Final = 5432

# Read-only twice over: the session refuses writes even if the customer granted
# a role that could perform them.
READ_ONLY_SESSION: Final = {"default_transaction_read_only": "on"}

# IAM database authentication requires TLS; RDS rejects the token otherwise.
RDS_SSL_MODE: Final = "require"

CONNECT_TIMEOUT_SECONDS: Final = 10


class DatabaseAuthError(RuntimeError):
    """The integration's auth mode cannot produce a connection."""


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    """Which database to read, and how we are allowed to authenticate to it."""

    host: str
    port: int
    database: str
    user: str
    auth: str
    grant: RoleGrant | None = None

    def __post_init__(self) -> None:
        if self.auth == AUTH_RDS_IAM and self.grant is None:
            raise DatabaseAuthError(
                f"{self.host}: IAM database authentication needs a role to assume"
            )
        if self.auth == AUTH_PASSWORD:
            raise DatabaseAuthError(
                f"{self.host}: password authentication for self-hosted Postgres is "
                "not implemented — this system stores no customer database "
                "passwords (ADR 0076). Use an RDS instance with IAM database "
                "authentication enabled."
            )
        if self.auth != AUTH_RDS_IAM:
            raise DatabaseAuthError(f"{self.host}: unknown auth mode {self.auth!r}")


def rds_auth_token(
    target: DatabaseTarget, *, integration_id: str, region: str, sts_client: Any | None = None
) -> str:
    """A password that is valid for fifteen minutes and belongs to nobody.

    Generated inside the customer's account, using the role they granted us, so
    the signature is theirs and their CloudTrail records the poll.
    """
    if target.grant is None:  # pragma: no cover - DatabaseTarget already refuses this
        raise DatabaseAuthError(f"{target.host}: no role grant")
    session = assume_customer_role(
        target.grant,
        session_name=session_name_for(integration_id),
        region=region,
        sts_client=sts_client,
    )
    rds = session.client("rds", region_name=region)
    token: str = rds.generate_db_auth_token(
        DBHostname=target.host, Port=target.port, DBUsername=target.user, Region=region
    )
    return token


async def connect_read_only(
    target: DatabaseTarget, *, integration_id: str, region: str
) -> asyncpg.Connection[Any]:
    """A connection that cannot write, authenticated without a stored secret."""
    password = rds_auth_token(target, integration_id=integration_id, region=region)
    connection: asyncpg.Connection[Any] = await asyncpg.connect(
        host=target.host,
        port=target.port,
        database=target.database,
        user=target.user,
        password=password,
        ssl=RDS_SSL_MODE,
        server_settings=READ_ONLY_SESSION,
        timeout=CONNECT_TIMEOUT_SECONDS,
    )
    return connection
