"""No API token reaches a dashboard route, through the real app with real tokens (F20,
T2.16, AC9, D9).

A one-project token reading organization-wide definitions would learn other projects'
service names, so tokens get no dashboard route at all. Each kind of real token, minted
the way `/settings/api-tokens` mints one and looked up by the real dependency, is refused
on every route; the batch spends no scan, which the ClickHouse client here would count;
and no dashboard path appears in the read API's published description. F4's assistant
ticket, the third kind, does not exist on this branch; it joins when F4 merges.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import pytest
from environment import TEST_ENVIRONMENT
from sqlalchemy.ext.asyncio import AsyncSession
from test_findings_cases_live import Team, _team, db  # noqa: F401

from nexovue_api.apitokens import service as api_tokens
from nexovue_api.data.postgres.models import Dashboard
from nexovue_api.deps import get_app_settings, get_clickhouse, get_db_session, get_session_store
from nexovue_api.main import create_app
from nexovue_api.settings import API_PREFIX, Settings

TOKEN_KINDS: Final = ("all_projects", "one_project")


class _CountingClickHouse:
    """A ClickHouse client that counts what it is asked, and answers nothing."""

    def __init__(self) -> None:
        self.queries = 0

    async def query(self, *_: Any, **__: Any) -> Any:
        self.queries += 1
        raise AssertionError("a scan ran for a token")


@pytest.fixture
async def acme(db: AsyncSession) -> Team:  # noqa: F811
    return await _team(db, "Tokens")


async def _token(db: AsyncSession, team: Team, kind: str) -> str:  # noqa: F811
    minted = await api_tokens.create(
        db,
        org_id=team.org_id,
        actor=team.owner,
        name=f"{kind} {uuid.uuid4().hex[:6]}",
        project_id=team.project_id if kind == "one_project" else None,
        expires_in_days=30,
        now=datetime.now(UTC),
    )
    return minted.token.get_secret_value()


def _app(db: AsyncSession, clickhouse: _CountingClickHouse) -> httpx.AsyncClient:  # noqa: F811
    settings = Settings(**TEST_ENVIRONMENT)  # type: ignore[arg-type]
    app = create_app(settings)

    async def session() -> Any:
        yield db

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_clickhouse] = lambda: clickhouse
    app.dependency_overrides[get_session_store] = lambda: None
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _routes(dashboard_id: uuid.UUID, project_id: uuid.UUID) -> list[tuple[str, str, Any]]:
    document = {
        "title": "From a token",
        "description": "",
        "definition": {
            "schema_version": 1,
            "refresh_seconds": 60,
            "default_range": None,
            "service_picker": False,
            "links": [],
            "panels": [],
        },
    }
    return [
        ("GET", "/dashboards", None),
        ("POST", "/dashboards", document),
        ("GET", "/dashboards/templates", None),
        ("GET", f"/dashboards/{dashboard_id}", None),
        ("PUT", f"/dashboards/{dashboard_id}", {**document, "version": 1}),
        ("DELETE", f"/dashboards/{dashboard_id}", None),
        ("GET", f"/dashboards/{dashboard_id}/context?project_id={project_id}", None),
        (
            "GET",
            f"/dashboard-panels/read?dashboard_id={dashboard_id}&project_id={project_id}&range=1h",
            None,
        ),
        (
            "POST",
            "/dashboard-panels/preview",
            {"project_id": str(project_id), "range": "1h", "definition": document["definition"]},
        ),
    ]


@pytest.mark.parametrize("kind", TOKEN_KINDS)
async def test_ac9_every_kind_of_token_is_refused_every_dashboard_route_spending_no_scan(
    db: AsyncSession,  # noqa: F811
    acme: Team,
    kind: str,
) -> None:
    dashboard = Dashboard(
        org_id=acme.org_id,
        title="Watched",
        definition={
            "schema_version": 1,
            "refresh_seconds": 60,
            "default_range": None,
            "service_picker": False,
            "links": [],
            "panels": [
                {
                    "id": "p_1",
                    "kind": "project_trend",
                    "title": None,
                    "width": 4,
                    "height": "medium",
                    "settings": {"signal": "rate"},
                }
            ],
        },
    )
    db.add(dashboard)
    await db.flush()
    token = await _token(db, acme, kind)
    clickhouse = _CountingClickHouse()

    async with _app(db, clickhouse) as client:
        for method, path, body in _routes(dashboard.id, acme.project_id):
            answer = await client.request(
                method,
                f"{API_PREFIX}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert answer.status_code == 403, (method, path, answer.status_code, answer.text)

    assert clickhouse.queries == 0
    await db.refresh(dashboard)
    assert (dashboard.title, dashboard.deleted_at, dashboard.last_viewed_at) == (
        "Watched",
        None,
        None,
    )


async def test_ac9_no_dashboard_route_is_in_the_read_apis_published_description(
    db: AsyncSession,  # noqa: F811
) -> None:
    async with _app(db, _CountingClickHouse()) as client:
        published = (await client.get(f"{API_PREFIX}/openapi.read.json")).json()

    paths = list(published["paths"])
    assert paths, "the published description lists no path at all"
    assert not [path for path in paths if "dashboard" in path]
