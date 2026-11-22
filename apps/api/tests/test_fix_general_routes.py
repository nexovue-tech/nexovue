"""Rung 2b's routes and its offer, with no store (F5 plan T2b.7; spec FR31, FR32, BR5; AC67,
AC74's token half, AC73's route list).

**AC67, one table:** **Suggest a general fix** is offered only when FR31 holds, and each
failing part alone hides it. Rung 3 open is not a part yet: no code location exists before
Step 3, so FR9 always reaches `no_code_location` (T3.7 adds the row).

**AC74:** every rung 2b route is a person's, so a token is refused below it with 403 before
any store is read, none is on the read surface, and no chat or MCP tool reads it.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from nexovue_platform.assistant_tools import CATALOG

from nexovue_api.apitokens.tokens import API_TOKEN_PREFIX, TOKENS_ARE_READ_ONLY
from nexovue_api.auth.dependencies import get_current_identity, get_current_reader
from nexovue_api.deps import get_app_settings, get_db_session, get_session_store
from nexovue_api.findings.fix_eligibility import (
    Eligibility,
    EligibilityFacts,
    general_eligibility,
)
from nexovue_api.findings.fixes import general_router
from nexovue_api.settings import API_PREFIX, Settings

NOW: Final = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
GENERAL: Final = "/findings/{finding_id}/fixes/general"
# `/check` is rung 3's own route since Step 4, `/patch` since Step 5 and
# `/draft-pull-request` since Step 7; each refuses a general row there (T4.12, T5.2, T7.4).
ON_CODE: Final = tuple(
    f"/findings/{{finding_id}}/fixes/{{fix_id}}/{action}" for action in ("run-ci",)
)
ALL_PATHS: Final = {GENERAL, *ON_CODE}

# FR31 holds: AI on, a service finding past its first read, a released cause sendable.
OFFERED: Final = EligibilityFacts(
    hosted=False,
    ai_on=True,
    opted_out=False,
    project_scope=False,
    closed_reason=None,
    opened_at=NOW - timedelta(minutes=30),
    error_group_linked=True,
    released_cause_rule="signed_deploy",
    sendable_facts=True,
    cause_or_error_alias=True,
    tokens_left=True,
    tries_used=0,
    in_flight=False,
)


@pytest.mark.parametrize(
    ("label", "change", "expected"),
    [
        ("FR31 holds", {}, Eligibility("no_code_location", offered=True)),
        ("hosted", {"hosted": True}, Eligibility("hosted_ai_not_offered", offered=False)),
        ("AI off", {"ai_on": False}, Eligibility("ai_off", offered=False)),
        ("project opted out", {"opted_out": True}, Eligibility("project_opted_out", offered=False)),
        (
            "a project finding",
            {"project_scope": True},
            Eligibility("project_finding", offered=False),
        ),
        (
            "closed as not a problem",
            {"closed_reason": "false_alarm"},
            Eligibility("closed_as_not_a_problem", offered=False),
        ),
        (
            "evidence pending",
            {"error_group_linked": False, "opened_at": NOW - timedelta(minutes=5)},
            Eligibility("evidence_pending", offered=False),
        ),
        (
            "cause is elsewhere",
            {"released_cause_rule": "dependency_finding"},
            Eligibility("cause_is_elsewhere", offered=False),
        ),
        (
            "no model_input",
            {"sendable_facts": False},
            Eligibility("no_code_location", offered=False),
        ),
        (
            "neither a likely cause nor an error alias",
            {"cause_or_error_alias": False},
            Eligibility("no_code_location", offered=False),
        ),
        ("F5's share spent", {"tokens_left": False}, Eligibility("limit_reached", offered=False)),
        ("three tries today", {"tries_used": 3}, Eligibility("limit_reached", offered=False)),
        ("a fix in flight", {"in_flight": True}, Eligibility("already_running", offered=False)),
    ],
)
def test_ac67_offered_only_when_fr31_holds_and_hidden_by_each_part_alone(
    label: str, change: dict[str, Any], expected: Eligibility
) -> None:
    assert general_eligibility(replace(OFFERED, **change), now=NOW) == expected, label


def test_evidence_stops_being_pending_fifteen_minutes_after_the_finding_opened() -> None:
    late = replace(OFFERED, error_group_linked=False, opened_at=NOW - timedelta(minutes=15))

    assert general_eligibility(late, now=NOW).offered


@pytest.mark.parametrize("reason", ["new_normal", "adopted", "service_gone"])
def test_fr9_every_closing_that_says_not_a_problem_hides_it(reason: str) -> None:
    found = general_eligibility(replace(OFFERED, closed_reason=reason), now=NOW)

    assert found == Eligibility("closed_as_not_a_problem", offered=False)


def test_a_finding_closed_as_recovered_may_still_be_offered() -> None:
    assert general_eligibility(replace(OFFERED, closed_reason="recovered"), now=NOW).offered


class _NothingStored:
    """A store a refused request must never reach."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a refused request reached the store ({name})")


@pytest.fixture
def app() -> FastAPI:
    from nexovue_api.main import create_app

    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    built = create_app(settings)

    async def no_db() -> AsyncIterator[Any]:
        yield _NothingStored()

    built.dependency_overrides[get_app_settings] = lambda: settings
    built.dependency_overrides[get_db_session] = no_db
    built.dependency_overrides[get_session_store] = lambda: _NothingStored()
    return built


async def test_ac74_an_api_token_gets_403_on_every_rung_2b_route(app: FastAPI) -> None:
    token = {"authorization": f"Bearer {API_TOKEN_PREFIX}{'a' * 40}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        ids = {"finding_id": uuid.uuid4(), "fix_id": uuid.uuid4()}
        read = await client.get(API_PREFIX + GENERAL.format(**ids), headers=token)
        writes = [
            await client.post(
                API_PREFIX + path.format(**ids), headers=token, json={"language": "go"}
            )
            for path in (GENERAL, *ON_CODE)
        ]

    for response in (read, *writes):
        assert response.status_code == 403
        assert response.json()["detail"] == TOKENS_ARE_READ_ONLY


def test_ac74_every_rung_2b_route_asks_for_a_person_and_never_a_reader() -> None:
    routes = _routes(general_router.router)

    assert {route.path for route in routes} == ALL_PATHS
    for route in routes:
        calls = _calls(route)
        assert get_current_identity in calls, route.path
        assert get_current_reader not in calls, route.path
        assert "CurrentReader" not in inspect.getsource(route.endpoint), route.path


def test_ac74_no_chat_or_mcp_tool_reads_a_suggestion() -> None:
    assert not [tool.name for tool in CATALOG if "/fixes" in tool.route]


def _calls(route: APIRoute) -> set[Any]:
    found: set[Any] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        found.add(dependency.call)
        pending.extend(dependency.dependencies)
    return found


def _routes(router: APIRouter) -> list[APIRoute]:
    return [route for route in router.routes if isinstance(route, APIRoute)]
