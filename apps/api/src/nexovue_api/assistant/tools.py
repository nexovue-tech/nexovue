"""What an assistant may ask for (F36, FR32-FR35).

Every tool is a read-surface request made in process: it calls the same service
function the HTTP route calls, with the token's own organization and scope, so a
tool cannot see what its endpoint would refuse (AC33). None of them writes — the
service functions they call are the ones behind `GET`s, and
`test_mcp_tools.py` holds that as a rule rather than a habit.

Each answer is bounded (`bounds.py`) and each window defaults to the last hour,
because "what is failing?" asked without a window is a question about now.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, Final

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Scope

from nexovue_api.alerts import service as alerts
from nexovue_api.assistant.auth import reader_of, resources_of
from nexovue_api.assistant.bounds import MAX_ROWS, bounded
from nexovue_api.auth.dependencies import Reader, get_tenant_repository
from nexovue_api.checks import service as checks
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.deploys import service as deploys
from nexovue_api.errorgroups import service as error_groups
from nexovue_api.errors.exceptions import DomainError, InvalidRequestError
from nexovue_api.logs import search as log_search
from nexovue_api.projects import service as projects
from nexovue_api.services import service as services
from nexovue_api.traces import service as traces
from nexovue_api.traces.queries import TraceFilters

DEFAULT_HOURS: Final = 1
MAX_HOURS: Final = 24 * 7

NO_PROJECT: Final = (
    "This token reads several projects, so name one: call list_projects and pass "
    "its id as project_id."
)


@dataclass(frozen=True, slots=True)
class Call:
    """One tool call's context: who is asking, and what it may reach."""

    reader: Reader
    db: AsyncSession
    repository: TenantScopedRepository
    project_id: uuid.UUID


def _scope(ctx: Context) -> Scope:
    """The HTTP request behind this tool call.

    Only ever absent on the stdio transport, which this server does not serve —
    it is mounted in the API and reached over HTTP. Raising here rather than
    reading around it keeps "the reader came from an authenticated request"
    true by construction.
    """
    request = ctx.request_context.request
    if request is None:  # pragma: no cover — mounted over HTTP only
        raise InvalidRequestError("This tool is served over HTTP.")
    scope: Scope = request.scope
    return scope


def window(hours: float) -> tuple[datetime, datetime]:
    """The time range a tool reads, bounded so one call cannot scan a year."""
    if hours <= 0 or hours > MAX_HOURS:
        raise InvalidRequestError(f"hours must be between 0 and {MAX_HOURS}.")
    now = datetime.now(UTC)
    return now - timedelta(hours=hours), now


@asynccontextmanager
async def opened(scope: Scope, project_id: str | None) -> AsyncIterator[Call]:
    """Everything one tool call needs, closed again when it ends.

    The session is this call's own: the MCP app is not a FastAPI route, so
    nothing else is opening or committing one around it. It is never committed —
    a tool reads.
    """
    stored = reader_of(scope)
    reader = Reader(
        org_id=stored["org_id"],
        project_scope=stored["project_scope"],
        token_id=stored["token_id"],
    )
    resources = resources_of(scope)
    async with resources.session_factory() as session:
        chosen = await _project_for(session, reader, project_id)
        yield Call(
            reader=reader,
            db=session,
            repository=get_tenant_repository(reader, await resources.clickhouse.client()),
            project_id=chosen,
        )


async def _project_for(session: AsyncSession, reader: Reader, named: str | None) -> uuid.UUID:
    """The project this call reads: the one named, or the token's own scope.

    A token scoped to one project needs no argument and may not escape it; a
    token that reads every project must say which, because "all of them" is not
    a window a single telemetry query can answer.
    """
    if named is not None:
        project = await projects.require_readable_project(
            session, reader=reader, project_id=uuid.UUID(named)
        )
        return project.id
    if reader.project_scope is not None:
        return reader.project_scope
    raise InvalidRequestError(NO_PROJECT)


def register(mcp: MCPServer) -> None:
    """Declare every tool on the server. Each one reads; none writes."""

    def tool(function: Callable[..., Any]) -> Callable[..., Any]:
        """Register one tool, and let its refusals reach the model.

        The SDK hides an unexpected exception's text — rightly, so an internal
        failure cannot leak into a conversation. A domain error here is the
        opposite: "name a project", "that window is too wide" are *answers*, and
        a model that is told only "error executing tool" cannot act on them.
        """

        @wraps(function)
        async def answered(*arguments: Any, **named: Any) -> Any:
            try:
                return await function(*arguments, **named)
            except DomainError as refusal:
                raise ToolError(refusal.detail) from refusal

        registered: Callable[..., Any] = mcp.tool()(answered)
        return registered

    # Declared by hand rather than through `opened`, which insists on a project:
    # this is the tool an assistant calls to learn which projects there are.
    @tool
    async def list_projects(ctx: Context) -> str:
        """Every project this token may read, with the id each other tool takes."""
        scope = _scope(ctx)
        stored = reader_of(scope)
        resources = resources_of(scope)
        async with resources.session_factory() as session:
            listed = await projects.list_for_organization(session, stored["org_id"])
        rows = [
            {"project_id": str(project.id), "slug": project.slug, "name": project.name}
            for project in listed.projects
            if stored["project_scope"] is None or project.id == stored["project_scope"]
        ]
        return bounded(rows, total=len(rows), subject="projects")

    @tool
    async def list_services(ctx: Context, project_id: str | None = None, hours: float = 1) -> str:
        """Every service that reported in the window, worst first: request rate,
        error rate and p95 latency. Start here when asked what is failing."""
        from_time, to_time = window(hours)
        async with opened(_scope(ctx), project_id) as call:
            answer = await services.list_services(
                call.repository,
                project_id=str(call.project_id),
                from_time=from_time,
                to_time=to_time,
                limit=MAX_ROWS,
            )
        rows = [service.model_dump() for service in answer.services]
        return bounded(rows, total=len(rows), subject="services")

    @tool
    async def get_service(
        ctx: Context, service_name: str, project_id: str | None = None, hours: float = 1
    ) -> str:
        """One service's health over the window. An empty answer means it was
        silent in that window, not that it does not exist."""
        from_time, to_time = window(hours)
        async with opened(_scope(ctx), project_id) as call:
            answer = await services.get_service(
                call.repository,
                project_id=str(call.project_id),
                service_name=service_name,
                from_time=from_time,
                to_time=to_time,
            )
        return bounded([answer.model_dump()], total=1, subject="service")

    @tool
    async def search_logs(
        ctx: Context,
        q: str | None = None,
        service: str | None = None,
        min_severity: int | None = None,
        project_id: str | None = None,
        hours: float = 1,
    ) -> str:
        """Log records matching a phrase, newest first. `min_severity` is the
        OTLP number: 9 is INFO, 13 WARN, 17 ERROR."""
        from_time, to_time = window(hours)
        async with opened(_scope(ctx), project_id) as call:
            answer = await log_search.search_logs(
                call.repository,
                project_id=str(call.project_id),
                from_time=from_time,
                to_time=to_time,
                filters=log_search.LogFilters(
                    service_name=service, min_severity=min_severity, search=q
                ),
                limit=MAX_ROWS,
                cursor=None,
            )
        rows = [record.model_dump() for record in answer.logs]
        return bounded(rows, subject="logs")

    @tool
    async def list_error_groups(
        ctx: Context, service: str | None = None, project_id: str | None = None, hours: float = 24
    ) -> str:
        """Distinct errors in the window, most frequent first, with how many
        times each happened and when it was last seen."""
        from_time, to_time = window(hours)
        async with opened(_scope(ctx), project_id) as call:
            answer = await error_groups.list_error_groups(
                call.repository,
                project_id=str(call.project_id),
                from_time=from_time,
                to_time=to_time,
                service_name=service,
                limit=MAX_ROWS,
                new_only=False,
            )
        rows = [group.model_dump() for group in answer.groups]
        return bounded(rows, total=len(rows), subject="error_groups")

    @tool
    async def list_incidents(ctx: Context, project_id: str | None = None) -> str:
        """Alert incidents: the open ones first, then the last thirty days."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await alerts.list_incidents(
                call.db, org_id=call.reader.org_id, project_id=call.project_id
            )
        rows = [incident.model_dump() for incident in [*answer.open, *answer.recent]]
        return bounded(rows, total=len(rows), subject="incidents")

    @tool
    async def list_checks(ctx: Context, project_id: str | None = None) -> str:
        """Synthetic checks and their confirmed state: what is up, what is down."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await checks.list_for_project(
                call.db,
                org_id=call.reader.org_id,
                project_id=call.project_id,
                available_locations=[],
            )
        rows = [check.model_dump() for check in answer.checks]
        return bounded(rows, total=len(rows), subject="checks")

    @tool
    async def list_deploys(ctx: Context, project_id: str | None = None, limit: int = 20) -> str:
        """Recent deploys, newest first — what changed before something broke."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await deploys.list_for_project(
                call.db,
                org_id=call.reader.org_id,
                project_id=call.project_id,
                limit=min(limit, MAX_ROWS),
            )
        rows = [event.model_dump() for event in answer.deploys]
        return bounded(rows, total=len(rows), subject="deploys")

    @tool
    async def list_traces(
        ctx: Context,
        service: str | None = None,
        project_id: str | None = None,
        hours: float = 1,
        limit: int = 20,
    ) -> str:
        """Recent traces, slowest-relevant first, with duration and status."""
        from_time, to_time = window(hours)
        async with opened(_scope(ctx), project_id) as call:
            answer = await traces.list_traces(
                call.repository,
                project_id=str(call.project_id),
                from_time=from_time,
                to_time=to_time,
                filters=TraceFilters(service_name=service),
                limit=min(limit, MAX_ROWS),
                cursor=None,
            )
        rows = [trace.model_dump() for trace in answer.traces]
        return bounded(rows, subject="traces")
