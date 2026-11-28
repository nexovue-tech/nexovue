"""The path from a fixture case to a finding (plan T2.7; design *Picking files*).

Writes spans into the dev ClickHouse carrying an `exception` event whose
`exception.stacktrace` names the fixture repository's files, in the form each language
prints them: a Python traceback, a Node stack from compiled TypeScript (`dist/*.js`),
and a Java stack with qualified class names. F3's error read then finds a new error
group in the service, and F3 links it to a finding on that service; the API's error
group read gives the stack F5's path matching (Step 3) starts from.

**Match rates then rest on these seeded shapes, and reports must say so.** Real stacks
vary in ways these do not: source-mapped lines, minified names, frames from other
services, async gaps. The compiled TypeScript lines here equal the source's, which a
real build rarely gives.

To open a finding on the dev stack, seed first, then make the service unhealthy with
`just detection-fault --service <name>`, which uses the same service name.

    cd tools/ai-eval && uv run python -m fix.seed_findings --org <uuid> --project <uuid> \\
        [--case py_stack_average ...] [--service checkout] [--count 20]

Dev stack only: it refuses any ClickHouse host but this machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlparse

from fix.fixtures import Case, load_cases

DEFAULT_CLICKHOUSE: Final = "clickhouse://nexovue:nexovue_dev@localhost:18123/nexovue"
LOCAL_HOSTS: Final = ("localhost", "127.0.0.1", "::1")
# How many occurrences of each case, a minute apart ending now (guesses).
DEFAULT_COUNT: Final = 20
EXCEPTION_EVENT: Final = "exception"
# The span's columns this writes; everything else takes its default.
COLUMNS: Final = (
    "timestamp",
    "received_at",
    "tenant_id",
    "project_id",
    "trace_id",
    "span_id",
    "service_name",
    "span_name",
    "span_kind",
    "status_code",
    "status_message",
    "exception_type",
    "exception_message",
    "events",
)
# A column a Node stack names, which no fixture records (the frames are seeded).
_NODE_COLUMN: Final = 5


def stacktrace(case: Case) -> str:
    """The stack as the case's language prints it, innermost frame where it prints it."""
    language = case.service.language
    if language == "python":
        lines = ["Traceback (most recent call last):"]
        for frame in reversed(case.frames):
            lines.append(f'  File "{frame.runtime_path}", line {frame.line}, in {frame.function}')
            if frame.line_text:
                lines.append(f"    {frame.line_text}")
        lines.append(f"{case.exception_type}: {case.message}")
        return "\n".join(lines)
    if language == "typescript":
        frames = [
            f"    at {frame.function} ({frame.runtime_path}:{frame.line}:{_NODE_COLUMN})"
            for frame in case.frames
        ]
        return "\n".join([f"{case.exception_type}: {case.message}", *frames])
    frames = [f"\tat {frame.function}({frame.runtime_path}:{frame.line})" for frame in case.frames]
    return "\n".join([f"{case.exception_type}: {case.message}", *frames])


def span_rows(
    case: Case,
    *,
    org_id: str,
    project_id: str,
    end: datetime,
    count: int,
    service: str | None = None,
) -> list[list[Any]]:
    """`count` failed spans, a minute apart up to `end`, each with the case's exception."""
    event = {
        "name": EXCEPTION_EVENT,
        "attributes": {
            "exception.type": case.exception_type,
            "exception.message": case.message,
            "exception.stacktrace": stacktrace(case),
        },
    }
    rows = []
    for back in range(count):
        # Aware: clickhouse-connect's insert reads a naive datetime as the machine's local time.
        at = (end - timedelta(minutes=back)).astimezone(UTC)
        rows.append(
            [
                at,
                at,
                org_id,
                project_id,
                uuid.uuid4().hex,
                uuid.uuid4().hex[:16],
                service or case.service.name,
                case.frames[0].function,
                "server",
                "error",
                case.message,
                case.exception_type,
                case.message,
                json.dumps([event]),
            ]
        )
    return rows


def require_local(dsn: str) -> None:
    host = urlparse(dsn).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing ClickHouse host {host}: this writes synthetic spans, dev only")


def _client(dsn: str) -> Any:
    import clickhouse_connect

    parsed = urlparse(dsn)
    return clickhouse_connect.get_client(
        host=parsed.hostname,
        port=parsed.port or 18123,
        username=parsed.username or "nexovue",
        password=parsed.password or "",
        database=(parsed.path or "/nexovue").lstrip("/"),
    )


def seed(
    cases: Sequence[Case],
    *,
    dsn: str,
    org_id: str,
    project_id: str,
    end: datetime,
    count: int,
    service: str | None = None,
) -> int:
    """Write each case's spans; the number of rows written."""
    require_local(dsn)
    client = _client(dsn)
    written = 0
    try:
        for case in cases:
            rows = span_rows(
                case, org_id=org_id, project_id=project_id, end=end, count=count, service=service
            )
            client.insert("spans", rows, column_names=list(COLUMNS))
            written += len(rows)
    finally:
        client.close()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed F5's fixture cases as error spans.")
    parser.add_argument("--clickhouse", default=DEFAULT_CLICKHOUSE)
    parser.add_argument("--org", required=True, type=uuid.UUID)
    parser.add_argument("--project", required=True, type=uuid.UUID)
    parser.add_argument("--case", action="append", help="a case id; every case if left out")
    parser.add_argument("--service", help="write every case under this service name")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    arguments = parser.parse_args(argv)
    cases = load_cases()
    if arguments.case:
        unknown = set(arguments.case) - {case.id for case in cases}
        if unknown:
            parser.error(f"unknown case: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case.id in arguments.case]
    written = seed(
        cases,
        dsn=arguments.clickhouse,
        org_id=str(arguments.org),
        project_id=str(arguments.project),
        end=datetime.now(UTC),
        count=arguments.count,
        service=arguments.service,
    )
    print(f"wrote {written} spans for {len(cases)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
