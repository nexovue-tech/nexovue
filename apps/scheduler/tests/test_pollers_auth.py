"""We hold no customer credential we can avoid holding (P1-37, ADR 0076).

The claim these tests defend is a negative one — that no code path stores or
requires a customer's AWS access key or database password — so most of them
assert that something is *refused*. A negative claim with no test is a comment.

AWS is never called: `botocore.stub.Stubber` answers STS and RDS from the
service model, so parameters are validated against AWS's own shapes without a
credential or a network.
"""

from __future__ import annotations

import re
import ssl
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import boto3
import certifi
import pytest
from botocore.stub import Stubber

from nexovue_scheduler.pollers import integrations, postgres_connect
from nexovue_scheduler.pollers.aws import RoleGrant, session_name_for
from nexovue_scheduler.pollers.postgres_connect import (
    AUTH_PASSWORD,
    AUTH_RDS_IAM,
    RDS_SSL_MODE,
    READ_ONLY_SESSION,
    DatabaseAddressError,
    DatabaseAuthError,
    DatabaseTarget,
    DatabaseTLSError,
    rds_auth_token,
    rds_tls_context,
    refuse_unreachable_host,
)

ACCOUNT_ID: Final = "123456789012"
ROLE_ARN: Final = f"arn:aws:iam::{ACCOUNT_ID}:role/NexovueReadOnly"
EXTERNAL_ID: Final = "9f3d1c7a-6b48-4a2e-9d51-0c7e2b4f8a13"
REGION: Final = "eu-west-1"
INTEGRATION_ID: Final = "01996d5e-9a40-7e02-a000-000000000202"


def _sts_response() -> dict[str, Any]:
    return {
        "Credentials": {
            "AccessKeyId": "ASIAEXAMPLEEXAMPLE00",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "FQoGZXIvYXdzEExampleSessionToken",
            "Expiration": datetime(2026, 5, 21, 12, 51, tzinfo=UTC),
        }
    }


def _cloudwatch_config() -> dict[str, Any]:
    return {
        "role_arn": ROLE_ARN,
        "external_id": EXTERNAL_ID,
        "region": REGION,
        "db_instance_identifiers": ["acme-orders-prod"],
    }


def _postgres_config() -> dict[str, Any]:
    return {
        "role_arn": ROLE_ARN,
        "external_id": EXTERNAL_ID,
        "host": "orders-db.internal",
        "port": 5432,
        "database": "shop",
        "user": "nexovue_readonly",
        "auth": AUTH_RDS_IAM,
    }


# --- what an integration is allowed to store --------------------------------


def test_a_stored_integration_holds_no_secret() -> None:
    """Both stored values are public: an ARN the customer will paste into a
    ticket, and an id whose only job is to be unguessable to a *third* party.
    Neither grants access on its own — the trust policy in the customer's
    account does, and they can revoke it without telling us."""
    for config in (_cloudwatch_config(), _postgres_config()):
        assert set(config) <= {
            "role_arn",
            "external_id",
            "region",
            "db_instance_identifiers",
            "host",
            "port",
            "database",
            "user",
            "auth",
            "interval_seconds",
        }
        for key in config:
            assert "password" not in key
            assert "secret" not in key
            assert "access_key" not in key


def test_a_cloudwatch_config_must_name_its_instances() -> None:
    """Named explicitly rather than discovered: a poller that expands to every
    instance in an account bills the customer for metrics they never asked for."""
    config = _cloudwatch_config()
    del config["db_instance_identifiers"]
    with pytest.raises(integrations.IntegrationConfigError):
        integrations.rds_instances_from(config)


def test_an_integration_without_an_external_id_will_not_load() -> None:
    """The ExternalId is what stops someone else persuading us to assume a role
    on their behalf, so a connector missing one cannot be polled at all."""
    with pytest.raises(integrations.IntegrationConfigError):
        integrations.role_grant_from(_cloudwatch_config(), None)


def test_the_external_id_is_not_read_from_customer_supplied_config() -> None:
    """It lives in its own column precisely so the customer cannot choose it.

    A config key would be a value they submit, and AWS is explicit that an
    ExternalId the caller can set defeats the purpose of having one.
    """
    config = _cloudwatch_config() | {"external_id": "chosen-by-the-customer"}
    grant = integrations.role_grant_from(config, "01a04e00-0000-7000-8000-000000000001")

    assert grant.external_id == "01a04e00-0000-7000-8000-000000000001"


def test_a_connector_is_scheduled_under_its_own_row_id() -> None:
    """One project may connect two AWS accounts, and a name collision would
    silently drop one of them."""
    definition = integrations.IntegrationDefinition(
        id=uuid.UUID(INTEGRATION_ID),
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind=integrations.KIND_CLOUDWATCH,
        config=_cloudwatch_config(),
        external_id=uuid.uuid4(),
        interval_seconds=300,
    )
    assert definition.schedule_name == f"integration:{INTEGRATION_ID}"
    assert definition.interval_seconds == 300


# --- how a database connection is authenticated -----------------------------


def test_a_database_target_defaults_to_iam_authentication() -> None:
    target = integrations.database_target_from(_postgres_config(), EXTERNAL_ID)
    assert target.auth == AUTH_RDS_IAM
    assert target.grant == RoleGrant(role_arn=ROLE_ARN, external_id=EXTERNAL_ID)


def test_self_hosted_password_auth_is_refused_and_points_somewhere() -> None:
    """Refused loudly rather than falling back to something weaker.

    We still store no database password: that needs a key-management scheme this
    project has not decided on, and inventing one badly is worse than the feature
    being absent. What changed with ADR 0100 is that this is no longer a dead end
    — the customer's own collector reads the same two queries inside their
    network — so the refusal has to name that route. Asserted on both halves,
    because a refusal that only says "no" sends them away for nothing.
    """
    config = _postgres_config() | {"auth": AUTH_PASSWORD}
    with pytest.raises(DatabaseAuthError) as refusal:
        integrations.database_target_from(config)
    message = str(refusal.value)
    assert "IAM" in message, "the refusal must say what IS supported"
    assert "Collector" in message, "the refusal must name the route that works"


def test_an_unknown_auth_mode_is_refused() -> None:
    with pytest.raises(DatabaseAuthError):
        integrations.database_target_from(_postgres_config() | {"auth": "trust"})


def test_iam_authentication_without_a_role_is_refused() -> None:
    with pytest.raises(DatabaseAuthError):
        DatabaseTarget(
            host="orders-db.internal",
            port=5432,
            database="shop",
            user="nexovue_readonly",
            auth=AUTH_RDS_IAM,
            grant=None,
        )


def test_the_database_password_is_minted_per_poll_from_the_customer_s_role() -> None:
    """The only "password" in this path is an RDS auth token signed inside the
    customer's account, valid for fifteen minutes, and never stored."""
    sts = boto3.client("sts", region_name=REGION, aws_access_key_id="x", aws_secret_access_key="y")
    target = integrations.database_target_from(_postgres_config(), EXTERNAL_ID)

    with Stubber(sts) as stub:
        stub.add_response(
            "assume_role",
            _sts_response(),
            {
                "RoleArn": ROLE_ARN,
                "RoleSessionName": session_name_for(INTEGRATION_ID),
                "ExternalId": EXTERNAL_ID,
                "DurationSeconds": 900,
            },
        )
        token = rds_auth_token(target, integration_id=INTEGRATION_ID, region=REGION, sts_client=sts)
        stub.assert_no_pending_responses()

    # generate_db_auth_token is a local SigV4 presign, not an API call, so this
    # is the real token shape rather than a stub's echo.
    assert token.startswith(f"orders-db.internal:5432/?Action=connect&DBUser={target.user}")
    assert "X-Amz-Security-Token=" in token, "the token must be signed by the assumed role"
    assert "X-Amz-Expires=900" in token


def test_the_monitoring_session_cannot_write() -> None:
    """Read-only is enforced on the session, not only requested of the customer:
    a monitoring connection that can write is one that eventually will."""
    assert READ_ONLY_SESSION == {"default_transaction_read_only": "on"}


def test_the_ssl_mode_verifies_the_certificate_and_the_hostname() -> None:
    """`require` was TLS with no verification at all, which is the defect.

    Asserted as a constant rather than through a connection, because the
    difference is invisible at runtime: both modes encrypt, both satisfy RDS,
    and only one checks who is on the other end. A future edit back to `require`
    would break nothing a functional test could see.
    """
    assert RDS_SSL_MODE == "verify-full"


def test_a_missing_ca_bundle_refuses_rather_than_connecting_unverified() -> None:
    """The tempting recovery is the defect wearing the clothes of resilience.

    A context that fell back to the system trust store would connect to an
    RDS instance only where Amazon's CA happens to be present, and would
    silently accept any certificate where a bundle was supplied but empty.
    """
    rds_tls_context.cache_clear()
    with pytest.raises(DatabaseTLSError, match="RDS_CA_BUNDLE_PATH"):
        rds_tls_context("")


def test_a_ca_bundle_path_that_is_not_a_file_is_named_in_the_refusal() -> None:
    """A typo in a path is the failure that actually happens, and "certificate
    verify failed" three layers down does not name it."""
    rds_tls_context.cache_clear()
    with pytest.raises(DatabaseTLSError, match="not a file"):
        rds_tls_context("/no/such/rds-bundle.pem")


def test_a_real_bundle_produces_a_context_that_checks_the_hostname(tmp_path: Path) -> None:
    """Both flags asserted, because `verify-full` is exactly the pair of them.

    `check_hostname` alone would accept a valid certificate for another host;
    `verify_mode` alone would accept a self-signed one for this host. The
    combination is what makes pinning an address safe, which is what unblocks
    T1-2.
    """
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(certifi.contents(), encoding="utf-8")

    rds_tls_context.cache_clear()
    context = rds_tls_context(str(bundle))

    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


# ------------------------------------------------------- the address boundary


async def test_a_host_resolving_to_loopback_is_refused() -> None:
    """The third SSRF surface, and the one that needs no attacker.

    `database_target_from` checks only that the host is a string, so until this
    a tenant could name `localhost` and the scheduler would connect to itself
    from inside our network. Door 2's HTTP check refuses this through
    `ConnectGuard` and its browser flow through `PublicAddressProxy`; door 4b
    went through neither.
    """
    with pytest.raises(DatabaseAddressError, match=re.escape("127.0.0.1")):
        await refuse_unreachable_host("localhost", 5432, private_allowed=False)


async def test_the_metadata_endpoint_is_refused_even_in_development() -> None:
    """169.254.169.254 is where every major cloud serves instance credentials.

    ADR 0098's line, restated for this door: link-local does not become safe to
    reach because somebody is developing, and a developer's laptop is exactly
    where a stolen role would be used. Asserted with the escape *on*, because
    that is the setting where a weaker rule would show.
    """
    with pytest.raises(DatabaseAddressError, match=re.escape("169.254.169.254")):
        await refuse_unreachable_host("169.254.169.254", 5432, private_allowed=True)


async def test_loopback_stays_refused_with_the_development_escape_on() -> None:
    """The other half of ADR 0098's line: loopback is the worker's own container."""
    with pytest.raises(DatabaseAddressError, match=re.escape("127.0.0.1")):
        await refuse_unreachable_host("127.0.0.1", 5432, private_allowed=True)


async def test_a_private_address_is_refused_by_default_and_allowed_in_development() -> None:
    """The one thing the escape opens: a private network the developer is inside.

    Both directions in one test, because the value of the flag is the whole
    difference and asserting only the permissive half would pass with the guard
    removed.
    """
    with pytest.raises(DatabaseAddressError, match=re.escape("10.0.0.1")):
        await refuse_unreachable_host("10.0.0.1", 5432, private_allowed=False)

    await refuse_unreachable_host("10.0.0.1", 5432, private_allowed=True)


async def test_a_public_address_is_allowed() -> None:
    """Without this the suite would pass with a guard that refused everything."""
    await refuse_unreachable_host("1.1.1.1", 5432, private_allowed=False)


async def test_a_name_that_does_not_resolve_is_refused_and_says_so() -> None:
    """Refused rather than passed through: asyncpg's own resolution failure
    arrives later and reads like the database being down."""
    with pytest.raises(DatabaseAddressError, match="does not resolve"):
        await refuse_unreachable_host("no-such-host.invalid", 5432, private_allowed=False)


async def test_connect_read_only_consults_the_boundary_before_connecting() -> None:
    """The line whose deletion nothing else would catch.

    Every test above exercises `refuse_unreachable_host` directly, so removing
    its one call site leaves them all green — the shape T3-4 records, where the
    only line that prevents a real leak is the only one with no behavioural
    test.

    Asserted by consequence rather than by inspection: a loopback host must
    raise *before* anything is attempted, which needs no database to prove and
    would fail differently if the guard ran too late.
    """
    target = integrations.database_target_from(
        _postgres_config() | {"host": "localhost"}, EXTERNAL_ID
    )

    with pytest.raises(DatabaseAddressError, match=re.escape("127.0.0.1")):
        await postgres_connect.connect_read_only(
            target,
            integration_id="i-1",
            region="eu-west-1",
            # Deliberately a path that does not exist: if the guard were to run
            # after TLS setup, this would raise DatabaseTLSError instead and the
            # test would say which of the two came first.
            ca_bundle_path="/no/such/bundle.pem",
            private_allowed=False,
        )
