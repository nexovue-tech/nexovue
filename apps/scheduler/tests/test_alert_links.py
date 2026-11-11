"""Every link a scheduler message composes resolves to a console route (FR43, AC54).

The scheduler cannot import the console, so the console's routes are checked in as
`apps/web/src/app/routes/console-routes.json`, which `route-manifest.test.ts` holds to
the router on the web side. This test composes one message of every kind that carries a
link — an incident's, a batch's, a finding's and a project notice's — and matches each
link, `manage_link` included, against that list. It exists because moving the front door
to `/` (F20 Step 1) silently broke the project notice's `/findings` link: nothing held
the two sides together.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nexovue_scheduler.alerts.messages import AlertMessage, compose, compose_batch
from nexovue_scheduler.alerts.outbox import Notice, Subject, SubjectKind

ROUTES_FILE = Path(__file__).resolve().parents[2] / "web/src/app/routes/console-routes.json"
BASE = "https://app.nexovue.test"
OPENED_AT = datetime(2026, 9, 19, 14, 3, tzinfo=UTC)
# One rule for every ruled subject: the manage link's `/alerts/rules/{id}`.
RULE_ID = uuid.uuid4()


def _console_routes() -> list[list[str]]:
    """The console's served paths, as segment lists; `$name` is a parameter."""
    paths = json.loads(ROUTES_FILE.read_text())
    assert isinstance(paths, list) and paths, "console-routes.json must list the routes"
    return [[segment for segment in path.split("/") if segment] for path in paths]


def _serves(link: str) -> bool:
    """True when the console serves `link`'s path with a screen of its own."""
    split = urlsplit(link)
    assert f"{split.scheme}://{split.netloc}" == BASE, f"link leaves the install: {link}"
    segments = [segment for segment in split.path.split("/") if segment]
    return any(
        len(route) == len(segments)
        and all(
            expected.startswith("$") or expected == actual
            for expected, actual in zip(route, segments, strict=True)
        )
        for route in _console_routes()
    )


def _subject(
    kind: SubjectKind,
    *,
    key: str = "check:probe",
    label: str = "Checkout API",
    rule_id: uuid.UUID | None = RULE_ID,
    rule_name: str | None = "Any check down",
    condition_kind: str = "check_state",
    detail: dict[str, Any] | None = None,
) -> Subject:
    return Subject(
        kind=kind,
        id=uuid.uuid4(),
        key=key,
        label=label,
        opened_at=OPENED_AT,
        closed_at=None,
        rule_id=rule_id,
        rule_name=rule_name,
        condition_kind=condition_kind,
        detail=detail if detail is not None else {"state": "down"},
    )


def _notice(subject: Subject, kind: str = "opened") -> Notice:
    return Notice(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind=kind,
        severity="critical",
        created_at=OPENED_AT,
        attempts=0,
        pagerduty_key="key",
        subject=subject,
    )


def _every_message() -> dict[str, AlertMessage]:
    """One message per composer branch that writes a link (messages.py)."""
    incident = _notice(_subject("incident"))
    finding = _notice(
        _subject(
            "finding",
            key="service:checkout",
            label="checkout",
            condition_kind="anomaly_finding",
            detail={"scope": "service", "title": "checkout is slower", "signals": []},
        )
    )
    project = _notice(
        _subject(
            "project",
            key="project",
            label="Production",
            rule_id=None,
            rule_name=None,
            condition_kind="detection",
            detail={},
        ),
        kind="stopped",
    )
    second = dataclasses.replace(
        incident, id=uuid.uuid4(), subject=dataclasses.replace(incident.subject, id=uuid.uuid4())
    )
    return {
        "incident": compose(incident, app_base_url=BASE),
        "finding": compose(finding, app_base_url=BASE),
        "project_notice": compose(project, app_base_url=BASE),
        "batch": compose_batch((incident, second), app_base_url=BASE),
    }


def test_every_link_a_message_composes_matches_a_console_route() -> None:
    for name, message in _every_message().items():
        assert _serves(message.link), f"{name}'s link has no console route: {message.link}"
        if message.manage_link is not None:
            assert _serves(message.manage_link), (
                f"{name}'s manage link has no console route: {message.manage_link}"
            )


def test_the_removed_findings_list_address_is_not_linked() -> None:
    """The regression that prompted this file: `/findings` no longer exists."""
    for name, message in _every_message().items():
        for link in (message.link, message.manage_link or ""):
            assert urlsplit(link).path.rstrip("/") != "/findings", (
                f"{name} still links to the removed /findings list"
            )
