"""Opening a read-only connection to a customer's Postgres without holding a password.

For RDS this is IAM database authentication: we assume the customer's role, ask
RDS to sign a token for a database user they created, and use that token as the
password. The token is valid for fifteen minutes and we never store it, so there
is no customer database password anywhere in this system.

Self-hosted Postgres has no equivalent, and is **not implemented**. Storing a
password would need envelope encryption and a key-management scheme we have not
decided on, and inventing one badly is worse than the feature being absent.
Configuring that auth mode fails loudly here rather than falling back to
something weaker.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import asyncpg

from nexovue_scheduler.pollers.aws import RoleGrant, assume_customer_role, session_name_for
from nexovue_scheduler.probes.addresses import REFUSAL_REASON, may_connect_to

AUTH_RDS_IAM: Final = "rds_iam"
AUTH_PASSWORD: Final = "password"

DEFAULT_PORT: Final = 5432

# Read-only twice over: the session refuses writes even if the customer granted
# a role that could perform them.
READ_ONLY_SESSION: Final = {"default_transaction_read_only": "on"}

# **Not `require`.** In asyncpg, `require` means TLS with no certificate
# verification at all — `check_hostname` is False and any certificate is
# accepted. That satisfies "IAM authentication needs TLS" and nothing else, which
# is why it went unnoticed: RDS rejects an unencrypted connection, so the weakest
# mode that gets past RDS looks like it worked.
#
# It matters because of what this connection carries. The whole argument for
# IAM authentication is that we hold no standing credential and mint a
# 15-minute token per poll; a token that can be intercepted in transit is a
# token we may as well have stored.
#
# `verify-full` also checks the hostname against the certificate, which is what
# makes pinning an address safe — the objection recorded against T1-2 was that
# "connecting to a judged IP literal would break TLS", and it is only true once
# verification exists.
RDS_SSL_MODE: Final = "verify-full"

# asyncpg does not support `sslrootcert=system`, so the CA has to be supplied.
# RDS certificates are signed by Amazon's own authority, which is not in every
# base image's trust store, and the bundle rotates — so it is a file the
# deployment provides rather than a constant compiled in here.
RDS_CA_BUNDLE_VARIABLE: Final = "RDS_CA_BUNDLE_PATH"

CONNECT_TIMEOUT_SECONDS: Final = 10


class DatabaseAddressError(RuntimeError):
    """The configured host resolves somewhere this poller may not go.

    Its text names the addresses, for our log; `customer_reason` is what the
    customer reads instead (group 9, A5).
    """


# What the customer reads when the database could not be reached at all: a
# network error's own text can carry the address we dialled.
UNREACHABLE_REASON: Final = "The database could not be reached from nexovue."


def customer_reason(failure: BaseException) -> str:
    """The failure as the integration's customer-visible last error.

    Never an address of ours: a refused host reads as the probes' refusal, and a
    network failure as unreachable. What the customer's own database answered —
    a missing permission, a wrong database name — is theirs to read and act on.
    """
    if isinstance(failure, DatabaseAddressError):
        return REFUSAL_REASON
    if isinstance(failure, asyncpg.PostgresError):
        return str(failure)
    if isinstance(failure, OSError | TimeoutError | asyncpg.InterfaceError):
        return UNREACHABLE_REASON
    return str(failure)


class DatabaseTLSError(RuntimeError):
    """TLS cannot be established the way this module requires.

    Its own type, and never caught to fall back: the failure it describes is a
    missing CA bundle, and the tempting recovery is to connect without
    verification — which is the defect this exists to prevent, wearing the
    clothes of resilience.
    """


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
            # Reachable only by a connector created before the API began refusing
            # this, so it is phrased for whoever reads the row's `last_error` —
            # which is a customer. It cites nothing internal they cannot read,
            # and now that their own collector can send the same data,
            # "not implemented" is the wrong answer anyway.
            raise DatabaseAuthError(
                f"{self.host}: this connector reads AWS RDS PostgreSQL with IAM "
                "database authentication, which is why it needs no password. For a "
                "database anywhere else, your own OpenTelemetry Collector can send "
                "the same data without a credential leaving your network — see the "
                "'Connecting a database' guide."
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


@lru_cache(maxsize=1)
def rds_tls_context(bundle_path: str) -> ssl.SSLContext:
    """The TLS context every poll shares, built once.

    Cached because loading and parsing a CA bundle on every poll is work with no
    answer that can change between two polls — and a poll runs per integration
    per minute.

    Refuses rather than degrades when the bundle is missing. `create_default_context`
    would happily fall back to the system store, which on a base image without
    Amazon's CA means every connection fails at the handshake with a message
    about an unknown issuer — true, and three steps away from the actual cause.
    """
    if not bundle_path:
        raise DatabaseTLSError(
            f"{RDS_CA_BUNDLE_VARIABLE} is not set, so an RDS certificate cannot be verified. "
            f"Point it at Amazon's RDS CA bundle. This is not optional: the connection carries "
            f"a database credential."
        )
    if not Path(bundle_path).is_file():
        raise DatabaseTLSError(f"{RDS_CA_BUNDLE_VARIABLE} names {bundle_path}, which is not a file")

    context = ssl.create_default_context(cafile=bundle_path)
    # Both are defaults of `create_default_context` and are set anyway, because
    # this is the line whose absence the whole change is about. Somebody reading
    # for "is the hostname checked" should find the answer here rather than infer
    # it from a library's defaults.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


async def refuse_unreachable_host(host: str, port: int, *, private_allowed: bool) -> None:
    """Resolve the configured host and refuse if any address is not public.

    **This is half of the boundary and the other half is TLS**, which is why G4
    had to land first and why neither alone would do.

    *This* half catches the case that needs no attacker: a tenant who configures
    `localhost`, `169.254.169.254`, or an address inside our own network, and a
    poller that would faithfully connect to it. `database_target_from` checks
    only that the host is a string, so until now nothing looked at where it
    pointed. That is the third SSRF surface, and the only one door 2 does not
    already close through `ConnectGuard` and `PublicAddressProxy`.

    **What it does not close is rebinding**, and it cannot. asyncpg does not
    expose `server_hostname`: `connect_utils` passes `server_hostname=host`, so
    the value used to connect is the value the certificate is checked against.
    Handing it a judged IP literal would make TLS fail, because an RDS
    certificate names the hostname and not the address — which is exactly the
    objection recorded against T1-2, and it is true. So the host is resolved,
    judged, and then connected to **by name**, leaving a window in which a second
    resolution could answer differently.

    `verify-full` closes that window. An attacker who rebinds the name to an
    internal address cannot present a certificate valid for the customer's
    database hostname, so the handshake fails. Resolution-time judgement stops
    the honest mistake; certificate verification stops the deliberate attack.
    Before G4 only the first would have existed, and it would have been a
    boundary in name.
    """
    try:
        resolved = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as failure:
        raise DatabaseAddressError(f"{host} does not resolve: {failure}") from failure

    addresses = {str(info[4][0]) for info in resolved}
    refused = sorted(
        address
        for address in addresses
        if not may_connect_to(address, private_allowed=private_allowed)
    )
    if refused:
        raise DatabaseAddressError(
            f"{host} resolved to {', '.join(refused)}, which this poller may not connect to. "
            f"A monitoring target must be reachable from the public internet."
        )


async def connect_read_only(
    target: DatabaseTarget,
    *,
    integration_id: str,
    region: str,
    ca_bundle_path: str,
    private_allowed: bool = False,
) -> asyncpg.Connection[Any]:
    """A connection that cannot write, authenticated without a stored secret.

    `ca_bundle_path` is passed rather than read from the environment here, for
    the same reason `private_allowed` is threaded through the probe boundary: a
    hidden module-level environment read is the kind of thing that survives into
    production without anyone choosing it.
    """
    await refuse_unreachable_host(target.host, target.port, private_allowed=private_allowed)
    password = rds_auth_token(target, integration_id=integration_id, region=region)
    connection: asyncpg.Connection[Any] = await asyncpg.connect(
        host=target.host,
        port=target.port,
        database=target.database,
        user=target.user,
        password=password,
        ssl=rds_tls_context(ca_bundle_path),
        server_settings=READ_ONLY_SESSION,
        timeout=CONNECT_TIMEOUT_SECONDS,
    )
    return connection
