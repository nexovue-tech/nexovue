"""Connector queries carry their tenant, and the ExternalId is ours (P1-37).

The statement-inspection technique is the same one `test_checks.py` uses and for
the same reason: a query that lost its `org_id` would still return correct-looking
rows against single-tenant fixture data.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from nexovue_api.errors.exceptions import InvalidRequestError
from nexovue_api.integrations import queries, service
from nexovue_api.integrations.schemas import CreateIntegrationRequest

ORG_ID = uuid.uuid4()
OTHER_ORG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
INTEGRATION_ID = uuid.uuid4()


class CapturingSession:
    """Records the statement it was handed and answers nothing."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def scalars(self, statement: Any) -> Any:
        self.statements.append(statement)
        return _Empty()

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return None

    @property
    def bound_values(self) -> set[Any]:
        return set(self.statements[-1].compile().params.values())


class _Empty:
    def all(self) -> list[Any]:
        return []


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


async def test_listing_connectors_binds_the_organization(session: CapturingSession) -> None:
    await queries.list_integrations(session, org_id=ORG_ID, project_id=PROJECT_ID)  # type: ignore[arg-type]

    assert ORG_ID in session.bound_values
    assert OTHER_ORG_ID not in session.bound_values


async def test_finding_one_connector_binds_the_organization(session: CapturingSession) -> None:
    await queries.find_integration(session, org_id=ORG_ID, integration_id=INTEGRATION_ID)  # type: ignore[arg-type]

    assert {ORG_ID, INTEGRATION_ID} <= session.bound_values


async def test_deleting_a_connector_binds_the_organization(session: CapturingSession) -> None:
    """Deleting by id alone would remove another tenant's connector."""
    await queries.delete_integration(session, org_id=ORG_ID, integration_id=INTEGRATION_ID)  # type: ignore[arg-type]

    assert {ORG_ID, INTEGRATION_ID} <= session.bound_values


def test_the_request_has_no_field_for_an_external_id() -> None:
    """The whole value of an ExternalId is that the caller cannot choose it.

    AWS is explicit: an id the caller supplies defeats the confused-deputy
    protection it exists to provide. So there is no field for one, and a client
    that sends anyway is ignored rather than obeyed.
    """
    assert "external_id" not in CreateIntegrationRequest.model_fields

    request = CreateIntegrationRequest.model_validate(
        {
            "project_id": str(PROJECT_ID),
            "kind": "cloudwatch",
            "external_id": "chosen-by-the-caller",
            "config": {"role_arn": "arn:aws:iam::123456789012:role/nexovue"},
        }
    )
    assert not hasattr(request, "external_id")


def test_a_self_hosted_postgres_connector_is_refused_at_creation() -> None:
    """The refusal has to happen here, not at the first poll.

    Without it the API answers 201 and the scheduler discovers a minute later
    that it cannot authenticate, so the customer sees success and then a
    `failing` row somewhere else, with nothing at the point of the mistake
    saying what was wrong.
    """
    request = CreateIntegrationRequest.model_validate(
        {
            "project_id": str(PROJECT_ID),
            "kind": "postgres",
            "config": {
                "host": "db.internal",
                "database": "orders",
                "user": "monitor",
                "auth": "password",
            },
        }
    )
    with pytest.raises(InvalidRequestError) as refusal:
        service._refuse_unsupported_target(request)

    message = str(refusal.value)
    # Both halves, because a refusal that only says "no" sends a customer away
    # for nothing when the collector gives them a route that works.
    assert "IAM" in message, "the refusal must say what IS supported"
    assert "Collector" in message, "the refusal must name the route that works"


def test_an_rds_iam_connector_is_accepted_and_so_is_omitting_the_mode() -> None:
    """`rds_iam` is the default, so a config that names no `auth` is the ordinary
    case and must not be refused by the check that exists for the other one."""
    for config in (
        {"host": "x.rds.amazonaws.com", "database": "orders", "user": "monitor"},
        {
            "host": "x.rds.amazonaws.com",
            "database": "orders",
            "user": "monitor",
            "auth": "rds_iam",
        },
    ):
        request = CreateIntegrationRequest.model_validate(
            {"project_id": str(PROJECT_ID), "kind": "postgres", "config": config}
        )
        service._refuse_unsupported_target(request)


def test_a_cloudwatch_connector_is_not_judged_by_the_postgres_rule() -> None:
    """It has one auth mode and no way to ask for another, so the check must not
    reach it — an `auth` key there means nothing and must not refuse the row."""
    request = CreateIntegrationRequest.model_validate(
        {
            "project_id": str(PROJECT_ID),
            "kind": "cloudwatch",
            "config": {"role_arn": "arn:aws:iam::123456789012:role/nexovue", "auth": "password"},
        }
    )
    service._refuse_unsupported_target(request)


def test_the_trust_policy_condition_names_the_generated_id() -> None:
    """Rendered for the customer rather than described. A trust policy missing
    this condition is one anybody can assume."""
    external_id = uuid.uuid4()

    condition = service._trust_policy_condition(external_id)

    assert condition == {"StringEquals": {"sts:ExternalId": str(external_id)}}
