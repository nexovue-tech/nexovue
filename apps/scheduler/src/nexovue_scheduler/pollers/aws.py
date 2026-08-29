"""Short-lived AWS credentials for one customer's account.

We hold no AWS credential belonging to a customer. The customer creates a role
in their own account whose trust policy names our account and a random
`ExternalId` we generated for their integration. At poll time we call STS
AssumeRole and receive credentials that expire in fifteen minutes.

Neither the role ARN nor the ExternalId is a secret, so the `integrations` row
holds nothing worth stealing, and revocation is the customer deleting a role
rather than us rotating a key we should never have had. ADR 0076 records the
decision and what it rejected.

The ExternalId is the part that is easy to leave out and expensive to omit:
without it, anyone who learns another customer's role ARN can ask us to poll it
and we would faithfully do so. AWS calls that the confused deputy problem. This
module therefore refuses to assume a role without one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

import boto3

# The shortest session AWS allows. A poll takes seconds, so credentials that
# outlive it by an hour are a liability with no benefit.
SESSION_DURATION_SECONDS: Final = 900

# `arn:aws:iam::<12-digit account>:role/<path and name>`. Parsed rather than
# trusted because the account id becomes `cloud.account.id` on every reading:
# a second config field holding the same number could disagree with this one.
_ROLE_ARN: Final = re.compile(r"^arn:aws[a-z-]*:iam::(\d{12}):role/(.+)$")

# AWS accepts 2-64 characters from this set in a RoleSessionName. The name shows
# up in the customer's CloudTrail, so it names us and the integration.
_SESSION_NAME_ALLOWED: Final = re.compile(r"[^\w+=,.@-]")
_SESSION_NAME_MAX_LENGTH: Final = 64


class RoleGrantError(ValueError):
    """An integration whose stored grant cannot be used to assume anything."""


@dataclass(frozen=True, slots=True)
class RoleGrant:
    """What a customer gives us so we can read their account: two public values."""

    role_arn: str
    external_id: str

    def __post_init__(self) -> None:
        if not _ROLE_ARN.match(self.role_arn):
            raise RoleGrantError(f"not an IAM role ARN: {self.role_arn!r}")
        if not self.external_id:
            # Refused here rather than at the API call, so no code path can
            # reach AssumeRole without one.
            raise RoleGrantError(f"role {self.role_arn} has no external id")

    @property
    def account_id(self) -> str:
        """The customer's AWS account, read off the ARN rather than stored twice."""
        match = _ROLE_ARN.match(self.role_arn)
        assert match is not None  # __post_init__ already refused anything else
        return match.group(1)


def session_name_for(integration_id: str) -> str:
    """What this poll is called in the customer's CloudTrail."""
    name = _SESSION_NAME_ALLOWED.sub("-", f"nexovue-{integration_id}")
    return name[:_SESSION_NAME_MAX_LENGTH]


def assume_customer_role(
    grant: RoleGrant,
    *,
    session_name: str,
    region: str,
    sts_client: Any | None = None,
) -> boto3.session.Session:
    """Exchange the grant for temporary credentials in the customer's account.

    Returns a session rather than raw keys so callers cannot accidentally keep
    the credentials past the object that owns them.
    """
    sts = sts_client if sts_client is not None else boto3.client("sts", region_name=region)
    response = sts.assume_role(
        RoleArn=grant.role_arn,
        RoleSessionName=session_name,
        ExternalId=grant.external_id,
        DurationSeconds=SESSION_DURATION_SECONDS,
    )
    credentials = response["Credentials"]
    return boto3.session.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )
