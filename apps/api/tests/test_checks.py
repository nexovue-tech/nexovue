"""Check queries carry their tenant in the statement (P1-33).

Every query here is captured before it reaches a database and its compiled
parameters are inspected. A query that lost its `org_id` predicate would still
return rows against a real Postgres if the test data happened to belong to the
right tenant — so asserting on the *statement* is what actually proves the
filter is there, rather than proving the fixture was lucky.

`test_checks_live.py` runs the same operations against real Postgres.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from nexovue_api.checks import queries, targets
from nexovue_api.errors.exceptions import InvalidRequestError

ORG_ID = uuid.uuid4()
OTHER_ORG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
CHECK_ID = uuid.uuid4()


class CapturingSession:
    """Records the statement it was handed and returns nothing.

    Deliberately not a database. The question is what the query asked for, and
    a real store would answer the question with data rather than with the query.
    """

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def scalars(self, statement: Any) -> Any:
        self.statements.append(statement)
        return _Empty()

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        return None

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return None

    @property
    def bound_values(self) -> dict[str, Any]:
        compiled = self.statements[-1].compile()
        return dict(compiled.params)


class _Empty:
    def all(self) -> list[Any]:
        return []


def _bound(session: CapturingSession) -> set[Any]:
    return set(session.bound_values.values())


@pytest.fixture
def session() -> CapturingSession:
    return CapturingSession()


async def test_listing_checks_binds_the_organization(session: CapturingSession) -> None:
    """A list scoped only by project would return another tenant's checks."""
    await queries.list_checks(session, org_id=ORG_ID, project_id=PROJECT_ID)  # type: ignore[arg-type]

    assert ORG_ID in _bound(session)
    assert PROJECT_ID in _bound(session)


async def test_finding_one_check_binds_the_organization(session: CapturingSession) -> None:
    """Fetching by id alone would let any tenant read any check by guessing one."""
    await queries.find_check(session, org_id=ORG_ID, check_id=CHECK_ID)  # type: ignore[arg-type]

    assert ORG_ID in _bound(session)
    assert CHECK_ID in _bound(session)


async def test_deleting_a_check_binds_the_organization(session: CapturingSession) -> None:
    """The dangerous one: a delete by id alone destroys another tenant's data."""
    await queries.delete_check(session, org_id=ORG_ID, check_id=CHECK_ID)  # type: ignore[arg-type]

    assert ORG_ID in _bound(session)
    assert CHECK_ID in _bound(session)


async def test_no_query_binds_an_organization_it_was_not_given(
    session: CapturingSession,
) -> None:
    """Guards the test itself: presence would pass even if org_id were ignored."""
    await queries.find_check(session, org_id=ORG_ID, check_id=CHECK_ID)  # type: ignore[arg-type]

    assert OTHER_ORG_ID not in _bound(session)


class TestTargetsWeRefuseToFetch:
    """A check makes our infrastructure fetch a URL somebody else chose.

    Unguarded, that is a request-forgery primitive: the metadata endpoint holds
    cloud credentials, and our own service names resolve inside our network.
    """

    @pytest.mark.parametrize(
        "target",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud instance credentials
            "http://127.0.0.1:8080/",
            "https://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://[::1]:5432/",
            "http://postgres:5432/",
            "http://localhost/",
        ],
    )
    def test_a_target_inside_our_network_is_refused(self, target: str) -> None:
        with pytest.raises(InvalidRequestError):
            targets.validate_target(target)

    @pytest.mark.parametrize(
        "target",
        [
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/",
            "",
            "   ",
            "https://",
        ],
    )
    def test_a_target_that_is_not_a_public_http_url_is_refused(self, target: str) -> None:
        with pytest.raises(InvalidRequestError):
            targets.validate_target(target)

    @pytest.mark.parametrize(
        "target",
        [
            "https://example.com",
            "https://shop.acme.example/health?deep=1",
            "http://example.com:8080/status",
            "https://93.184.216.34/",  # a public literal address is fine
        ],
    )
    def test_a_public_target_is_accepted(self, target: str) -> None:
        assert targets.validate_target(target) == target.strip()

    @pytest.mark.parametrize(
        "step_url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/",
            "http://postgres:5432/",
            "file:///etc/passwd",
        ],
    )
    def test_a_browser_step_cannot_reach_where_a_target_cannot(self, step_url: str) -> None:
        """The second door into a request, which the first version left open.

        A browser check carries its own step list, and each `goto` names a URL.
        Validating only the target meant a check pointed at a public site could
        still walk the prober to the metadata endpoint, with `expect_text`
        reporting whether a given string came back.
        """
        with pytest.raises(InvalidRequestError):
            targets.validate_step_urls({"steps": [{"action": "goto", "url": step_url}]})

    def test_a_flow_of_public_steps_is_accepted(self) -> None:
        targets.validate_step_urls(
            {
                "steps": [
                    {"action": "goto", "url": "https://shop.acme.example/"},
                    {"action": "expect_text", "selector": "h1", "text": "Shop"},
                ]
            }
        )

    def test_a_malformed_step_list_is_refused_rather_than_ignored(self) -> None:
        """Silently skipping something we cannot read is how a check gets stored
        that nobody validated."""
        with pytest.raises(InvalidRequestError):
            targets.validate_step_urls({"steps": "not a list"})
        with pytest.raises(InvalidRequestError):
            targets.validate_step_urls({"steps": ["not a step"]})
        with pytest.raises(InvalidRequestError):
            targets.validate_step_urls({"steps": [{"action": "goto", "url": 42}]})

    def test_an_overlong_target_is_refused(self) -> None:
        """Bounded so the column and every log line that quotes it stay sane."""
        with pytest.raises(InvalidRequestError):
            targets.validate_target("https://example.com/" + "a" * targets.MAX_TARGET_LENGTH)
