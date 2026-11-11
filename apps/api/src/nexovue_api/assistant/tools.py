"""What an assistant may ask for (F36, FR32-FR35; F4 Step 1, FR33-FR36).

Every tool is a read-surface request made in process: it calls the same service
function the HTTP route calls, with the token's own organization and scope, so a
tool cannot see what its endpoint would refuse (AC33). None of them writes — the
service functions they call are the ones behind `GET`s, and
`test_mcp_tools.py` holds that as a rule rather than a habit.

Each answer is bounded (`bounds.py`) and names its untrusted fields (FR33): the
reader is a customer's own AI, and a log body or a service name is text written
by whoever reached that customer's services. Each window defaults to the last
hour, because "what is failing?" asked without a window is a question about
now, and takes an exact past range, because "yesterday 14:00 to 14:30" is a
question too (FR34).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, Final, cast
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from nexovue_platform.checks.config import (
    ApiCheckConfig,
    CheckConfig,
    CheckType,
    DnsCheckConfig,
    MultistepCheckConfig,
    TcpCheckConfig,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Scope

from nexovue_api.alerts import service as alerts
from nexovue_api.assistant.auth import reader_of, resources_of
from nexovue_api.assistant.bounds import MAX_ROWS, bounded
from nexovue_api.auth.dependencies import Reader, get_tenant_repository
from nexovue_api.checks import service as checks
from nexovue_api.checks.schemas import CheckSummary, DisplayedState
from nexovue_api.data.clickhouse.base import TenantScopedRepository
from nexovue_api.deploys import service as deploys
from nexovue_api.detection import service as detection
from nexovue_api.errorgroups import service as error_groups
from nexovue_api.errors.exceptions import DomainError, InvalidRequestError
from nexovue_api.findings import service as findings
from nexovue_api.findings.schemas import FeedState
from nexovue_api.logs import search as log_search
from nexovue_api.projects import service as projects
from nexovue_api.servicemap import service as service_map
from nexovue_api.services import service as services
from nexovue_api.traces import service as traces
from nexovue_api.traces.queries import TraceFilters

DEFAULT_HOURS: Final = 1
# Errors are asked about after the fact, so their default window is a day.
DEFAULT_ERROR_HOURS: Final = 24
MAX_HOURS: Final = 24 * 7

NO_PROJECT: Final = (
    "This token reads several projects, so name one: call list_projects and pass "
    "its id as project_id."
)

FEED_STATES: Final = ("open", "closed", "all")
BAD_STATE: Final = "state must be one of open, closed or all."

# Which fields of each tool's answer hold text written outside nexovue (FR33):
# log bodies and attributes, names people or senders chose, error messages,
# config somebody typed. One registry, so the tools, their labels and the
# injection suite (`test_mcp_injection.py`, which walks the response models)
# cannot drift apart. Ids, times, numbers and closed vocabularies are not here,
# and the suite holds that everything else is.
UNTRUSTED_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "list_projects": ("projects[].name", "projects[].slug"),
    "list_services": ("services[].service_name",),
    "get_service": ("service[].service.service_name",),
    "search_logs": (
        "logs[].body",
        "logs[].attributes",
        "logs[].service_name",
        "logs[].severity",
    ),
    "list_error_groups": (
        "error_groups[].exception_type",
        "error_groups[].message_template",
        "error_groups[].latest_message",
        "error_groups[].grouped_by",
        "error_groups[].services",
        "error_groups[].preceding_deploy.service_name",
        "error_groups[].preceding_deploy.version",
    ),
    "list_incidents": (
        "incidents[].rule_name",
        "incidents[].subject_key",
        "incidents[].subject_label",
        "incidents[].detail",
        "incidents[].notifications[].channel_name",
        "incidents[].notifications[].reason",
        # F19: a burn's joined findings carry nexovue's titles, which name services.
        "incidents[].burn.findings[].title",
    ),
    "list_checks": ("checks[].name", "checks[].url_hosts"),
    "list_deploys": (
        "deploys[].version",
        "deploys[].service_name",
        "deploys[].source",
        "deploys[].metadata",
    ),
    "list_traces": (
        "traces[].root_service",
        "traces[].root_name",
        "traces[].span_services",
        "traces[].span_endpoints[].service_name",
        "traces[].span_endpoints[].operation",
    ),
    "list_findings": (
        "findings[].title",
        "findings[].service_name",
        "findings[].signals[].key",
        "findings[].signals[].comparison",
        "findings[].signals[].endpoint",
    ),
    "get_finding": (
        "finding[].title",
        "finding[].service_name",
        "finding[].signals[].key",
        "finding[].signals[].comparison",
        "finding[].signals[].endpoint",
        "finding[].services",
        "finding[].incidents[].rule_name",
        # F28: release names come from telemetry or a deploy sender (FR32, AC43).
        "finding[].release_context.before.service_name",
        "finding[].release_context.before.version",
        "finding[].release_context.nearby[].service_name",
        "finding[].release_context.nearby[].version",
        "finding[].release_context.affected[].service_name",
        "finding[].release_context.affected[].version",
        # F19: nexovue's label for a linked burn, composed from service and endpoint names.
        "finding[].incidents[].burn.label",
    ),
    "get_trace": (
        "trace[].spans[].service_name",
        "trace[].spans[].span_name",
        "trace[].spans[].status_message",
        "trace[].spans[].http_method",
        "trace[].spans[].http_route",
        "trace[].spans[].rpc_system",
        "trace[].spans[].rpc_method",
        "trace[].spans[].rpc_status",
        "trace[].spans[].exception_type",
        "trace[].spans[].exception_message",
        "trace[].spans[].deployment_version",
        "trace[].spans[].attributes",
        "trace[].spans[].events[].name",
        "trace[].spans[].events[].attributes",
        "trace[].spans[].links[].attributes",
    ),
    "get_error_group": (
        "error_group[].group.exception_type",
        "error_group[].group.message_template",
        "error_group[].group.latest_message",
        "error_group[].group.grouped_by",
        "error_group[].group.services",
        "error_group[].group.preceding_deploy.service_name",
        "error_group[].group.preceding_deploy.version",
        "error_group[].recent[].service_name",
        "error_group[].recent[].exception_message",
        "error_group[].recent[].stacktrace",
        "error_group[].recent[].release",
    ),
    "get_incident": (
        "incident[].rule_name",
        "incident[].subject_key",
        "incident[].subject_label",
        "incident[].detail",
        "incident[].notifications[].channel_name",
        "incident[].notifications[].reason",
        # F19: a burn's joined findings carry nexovue's titles, which name services.
        "incident[].burn.findings[].title",
    ),
    "get_service_map": (
        "service_map[].services",
        "service_map[].edges[].caller",
        "service_map[].edges[].callee",
        "service_map[].edges[].callee_kind",
    ),
    # Closed states, numbers and times only — nothing anybody worded.
    "get_service_detection": (),
}


@dataclass(frozen=True, slots=True)
class Call:
    """One tool call's context: who is asking, and what it may reach."""

    reader: Reader
    db: AsyncSession
    repository: TenantScopedRepository
    project_id: uuid.UUID


class CheckRow(BaseModel):
    """What `list_checks` sends: a check's identity and state, never its config.

    A config can hold literal URLs, query values, headers and bodies a person
    typed, and intake masking never sees it (FR35). The host says what the
    check probes; everything else stays in the console.
    """

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: CheckType
    url_hosts: list[str]
    interval_seconds: int
    enabled: bool
    displayed_state: DisplayedState | None
    state_since: datetime | None
    last_run_at: datetime | None
    last_ping_at: datetime | None


def check_row(check: CheckSummary) -> dict[str, Any]:
    """One check as the assistant may see it (FR35)."""
    return CheckRow(
        id=check.id,
        project_id=check.project_id,
        name=check.name,
        type=check.type,
        url_hosts=_url_hosts(check.config),
        interval_seconds=check.interval_seconds,
        enabled=check.enabled,
        displayed_state=check.displayed_state,
        state_since=check.state_since,
        last_run_at=check.last_run_at,
        last_ping_at=check.last_ping_at,
    ).model_dump()


def _url_hosts(config: CheckConfig) -> list[str]:
    """The hosts a check touches: enough to say what it probes, nothing more."""
    if isinstance(config, ApiCheckConfig):
        named = [urlsplit(config.request.url).hostname]
    elif isinstance(config, MultistepCheckConfig):
        named = [urlsplit(step.url).hostname for step in config.steps]
    elif isinstance(config, TcpCheckConfig):
        named = [config.host]
    elif isinstance(config, DnsCheckConfig):
        named = [config.name]
    else:  # a heartbeat is called by the customer; it probes nothing
        named = []
    return [host for host in named if host]


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


def window(
    hours: float | None,
    from_time: str | None = None,
    to_time: str | None = None,
    *,
    default_hours: float = DEFAULT_HOURS,
) -> tuple[datetime, datetime]:
    """The range a tool reads: recent hours, or an exact past range (FR34).

    One form or the other, never both — a call carrying both is a contradiction
    only the caller can resolve, and guessing would silently answer a different
    question than the one asked. Either form is bounded, so one call cannot
    scan a year.
    """
    if hours is not None and (from_time is not None or to_time is not None):
        raise InvalidRequestError("Give hours or a fixed range (from_time and to_time), not both.")
    if (from_time is None) != (to_time is None):
        raise InvalidRequestError(
            "A fixed range needs both from_time and to_time, as ISO 8601 times in UTC."
        )
    if from_time is not None and to_time is not None:
        start = _moment(from_time, name="from_time")
        end = _moment(to_time, name="to_time")
        if end <= start:
            raise InvalidRequestError("to_time must be after from_time.")
        if end - start > timedelta(hours=MAX_HOURS):
            raise InvalidRequestError("A range is at most 7 days per read. Narrow it.")
        return start, end
    chosen = default_hours if hours is None else hours
    if chosen <= 0 or chosen > MAX_HOURS:
        raise InvalidRequestError(f"hours must be between 0 and {MAX_HOURS}.")
    now = datetime.now(UTC)
    return now - timedelta(hours=chosen), now


def _moment(value: str, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as wrong:
        raise InvalidRequestError(
            f"{name} must be an ISO 8601 time, like 2026-11-01T14:00:00Z."
        ) from wrong
    # A bare time is read as UTC (FR34): the one zone every log line shares.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _identifier(value: str, *, name: str, source: str) -> uuid.UUID:
    """An id argument, or a refusal that says where ids come from.

    `uuid.UUID("nonsense")` raises a bare ValueError, which the tool wrapper
    rightly hides — so without this, a model typing a slug where an id belongs
    is told only "error executing tool", which it cannot act on (FR35).
    """
    try:
        return uuid.UUID(value)
    except ValueError as wrong:
        raise InvalidRequestError(f"{name} must be an id as {source} returns it.") from wrong


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
            session,
            reader=reader,
            project_id=_identifier(named, name="project_id", source="list_projects"),
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
        return bounded(
            rows,
            total=len(rows),
            subject="projects",
            untrusted=UNTRUSTED_FIELDS["list_projects"],
        )

    @tool
    async def list_services(
        ctx: Context,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> str:
        """Every service that reported in the window, worst first: request rate,
        error rate and p95 latency. Start here when asked what is failing."""
        window_start, window_end = window(hours, from_time, to_time)
        async with opened(_scope(ctx), project_id) as call:
            answer = await services.list_services(
                call.repository,
                project_id=str(call.project_id),
                from_time=window_start,
                to_time=window_end,
                limit=MAX_ROWS,
            )
        rows = [service.model_dump() for service in answer.services]
        return bounded(
            rows,
            total=len(rows),
            subject="services",
            untrusted=UNTRUSTED_FIELDS["list_services"],
        )

    @tool
    async def get_service(
        ctx: Context,
        service_name: str,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> str:
        """One service's health over the window. An empty answer means it was
        silent in that window, not that it does not exist."""
        window_start, window_end = window(hours, from_time, to_time)
        async with opened(_scope(ctx), project_id) as call:
            answer = await services.get_service(
                call.repository,
                project_id=str(call.project_id),
                service_name=service_name,
                from_time=window_start,
                to_time=window_end,
            )
        return bounded(
            [answer.model_dump()],
            total=1,
            subject="service",
            untrusted=UNTRUSTED_FIELDS["get_service"],
        )

    @tool
    async def search_logs(
        ctx: Context,
        q: str | None = None,
        service: str | None = None,
        min_severity: int | None = None,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> str:
        """Log records matching a phrase, newest first. `min_severity` is the
        OTLP number: 9 is INFO, 13 WARN, 17 ERROR."""
        window_start, window_end = window(hours, from_time, to_time)
        async with opened(_scope(ctx), project_id) as call:
            answer = await log_search.search_logs(
                call.repository,
                project_id=str(call.project_id),
                from_time=window_start,
                to_time=window_end,
                filters=log_search.LogFilters(
                    service_name=service, min_severity=min_severity, search=q
                ),
                limit=MAX_ROWS,
                cursor=None,
            )
        rows = [record.model_dump() for record in answer.logs]
        return bounded(rows, subject="logs", untrusted=UNTRUSTED_FIELDS["search_logs"])

    @tool
    async def list_error_groups(
        ctx: Context,
        service: str | None = None,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> str:
        """Distinct errors in the window, most frequent first, with how many
        times each happened and when it was last seen."""
        window_start, window_end = window(
            hours, from_time, to_time, default_hours=DEFAULT_ERROR_HOURS
        )
        async with opened(_scope(ctx), project_id) as call:
            answer = await error_groups.list_error_groups(
                call.repository,
                project_id=str(call.project_id),
                from_time=window_start,
                to_time=window_end,
                service_name=service,
                limit=MAX_ROWS,
                new_only=False,
            )
        rows = [group.model_dump() for group in answer.groups]
        return bounded(
            rows,
            total=len(rows),
            subject="error_groups",
            untrusted=UNTRUSTED_FIELDS["list_error_groups"],
        )

    @tool
    async def get_error_group(
        ctx: Context,
        group_id: str,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> str:
        """One error group in the window and its recent instances, with messages
        and stack traces. A group is derived per window: "not in this window" is
        the honest answer rather than "never happened"."""
        window_start, window_end = window(
            hours, from_time, to_time, default_hours=DEFAULT_ERROR_HOURS
        )
        async with opened(_scope(ctx), project_id) as call:
            answer = await error_groups.get_error_group(
                call.repository,
                project_id=str(call.project_id),
                group_id=group_id,
                from_time=window_start,
                to_time=window_end,
            )
        return bounded(
            [answer.model_dump()],
            total=1,
            subject="error_group",
            untrusted=UNTRUSTED_FIELDS["get_error_group"],
        )

    @tool
    async def list_incidents(ctx: Context, project_id: str | None = None) -> str:
        """Alert incidents: the open ones first, then the last thirty days."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await alerts.list_incidents(
                call.db, org_id=call.reader.org_id, project_id=call.project_id
            )
        rows = [incident.model_dump() for incident in [*answer.open, *answer.recent]]
        return bounded(
            rows,
            total=len(rows),
            subject="incidents",
            untrusted=UNTRUSTED_FIELDS["list_incidents"],
        )

    @tool
    async def get_incident(ctx: Context, incident_id: str, project_id: str | None = None) -> str:
        """One alert incident: its rule, subject, severity, when it opened and
        resolved, and which channels were told."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await alerts.get_incident(
                call.db,
                org_id=call.reader.org_id,
                incident_id=_identifier(incident_id, name="incident_id", source="list_incidents"),
                project_scope=call.project_id,
            )
        return bounded(
            [answer.model_dump()],
            total=1,
            subject="incident",
            untrusted=UNTRUSTED_FIELDS["get_incident"],
        )

    @tool
    async def list_checks(ctx: Context, project_id: str | None = None) -> str:
        """Synthetic checks and their confirmed state: what is up, what is down.
        Each row names the probed host; the full config stays in the console."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await checks.list_for_project(
                call.db,
                org_id=call.reader.org_id,
                project_id=call.project_id,
                available_locations=[],
            )
        rows = [check_row(check) for check in answer.checks]
        return bounded(
            rows, total=len(rows), subject="checks", untrusted=UNTRUSTED_FIELDS["list_checks"]
        )

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
        return bounded(
            rows, total=len(rows), subject="deploys", untrusted=UNTRUSTED_FIELDS["list_deploys"]
        )

    @tool
    async def list_traces(
        ctx: Context,
        service: str | None = None,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        limit: int = 20,
    ) -> str:
        """Recent traces, slowest-relevant first, with duration and status.
        Traces are sampled: this is a list, never a count."""
        window_start, window_end = window(hours, from_time, to_time)
        async with opened(_scope(ctx), project_id) as call:
            answer = await traces.list_traces(
                call.repository,
                project_id=str(call.project_id),
                from_time=window_start,
                to_time=window_end,
                filters=TraceFilters(service_name=service),
                limit=min(limit, MAX_ROWS),
                cursor=None,
            )
        rows = [trace.model_dump() for trace in answer.traces]
        return bounded(rows, subject="traces", untrusted=UNTRUSTED_FIELDS["list_traces"])

    @tool
    async def get_trace(ctx: Context, trace_id: str, project_id: str | None = None) -> str:
        """One trace and all its spans, flat: timings, status, errors and the
        attributes each span carried."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await traces.get_trace(
                call.repository, project_id=str(call.project_id), trace_id=trace_id
            )
        return bounded(
            [answer.model_dump()],
            total=1,
            subject="trace",
            untrusted=UNTRUSTED_FIELDS["get_trace"],
        )

    @tool
    async def list_findings(
        ctx: Context,
        project_id: str | None = None,
        state: str = "open",
        muted: bool = False,
    ) -> str:
        """Problems nexovue's anomaly detection found, worst first: what is
        slower, failing or losing traffic against its own normal. `state` is
        open, closed or all; `muted` True reads the muted ones the feed hides."""
        if state not in FEED_STATES:
            raise InvalidRequestError(BAD_STATE)
        async with opened(_scope(ctx), project_id) as call:
            answer = await findings.list_findings(
                call.db,
                call.repository,
                org_id=call.reader.org_id,
                project_id=call.project_id,
                state=cast(FeedState, state),
                muted=muted,
                # A tool's caller is a token's reach: rows name nobody (F22 FR20).
                for_person=False,
                now=datetime.now(UTC),
            )
        rows = [finding.model_dump() for finding in answer.findings]
        return bounded(
            rows,
            total=len(rows),
            subject="findings",
            untrusted=UNTRUSTED_FIELDS["list_findings"],
        )

    @tool
    async def get_finding(ctx: Context, finding_id: str, project_id: str | None = None) -> str:
        """One finding and its page: signals against their normal bands, linked
        incidents, and a project finding's services."""
        async with opened(_scope(ctx), project_id) as call:
            answer = await findings.get_finding(
                call.db,
                call.repository,
                org_id=call.reader.org_id,
                finding_id=_identifier(finding_id, name="finding_id", source="list_findings"),
                project_scope=call.project_id,
                for_member=False,
                now=datetime.now(UTC),
            )
        return bounded(
            [answer.model_dump()],
            total=1,
            subject="finding",
            untrusted=UNTRUSTED_FIELDS["get_finding"],
        )

    @tool
    async def get_service_map(
        ctx: Context,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> str:
        """The window's dependency graph, busiest edges first: who calls whom,
        how often, and how often it fails."""
        window_start, window_end = window(hours, from_time, to_time)
        async with opened(_scope(ctx), project_id) as call:
            answer = await service_map.get_service_map(
                call.repository,
                project_id=str(call.project_id),
                from_time=window_start,
                to_time=window_end,
                limit=MAX_ROWS,
            )
        return bounded(
            [answer.model_dump()],
            total=1,
            subject="service_map",
            untrusted=UNTRUSTED_FIELDS["get_service_map"],
        )

    @tool
    async def get_service_detection(
        ctx: Context,
        service_name: str,
        project_id: str | None = None,
        hours: float | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> str:
        """One service's detection state: whether it is judged or still
        learning, each signal's phase, and its findings in the window."""
        window_start, window_end = window(hours, from_time, to_time)
        async with opened(_scope(ctx), project_id) as call:
            answer = await detection.service_detection(
                call.db,
                org_id=call.reader.org_id,
                project_id=call.project_id,
                service_name=service_name,
                start=window_start,
                end=window_end,
                now=datetime.now(UTC),
            )
        return bounded(
            [answer.model_dump()],
            total=1,
            subject="detection",
            untrusted=UNTRUSTED_FIELDS["get_service_detection"],
        )
