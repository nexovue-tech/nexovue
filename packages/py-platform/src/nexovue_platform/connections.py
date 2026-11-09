"""What may cross to a tracker or into an email (F22, D39, D48).

A service name is someone else's words. Before it leaves this server — in an
issue (Step 4) or in the assignment email (FR5) — it becomes its strict form:
letters, digits and `_ / -` only, cut to 64, so it cannot read as a domain, a
mention or a sentence. A name that looks like a person's data — an `@` with text
on both sides, or a run of six or more digits — never leaves at all and reads as
the fixed words instead (AC82). The check runs before the strict form replaces
`@`, or `a@b` would slip through as `a_b`.

It also holds the connection vocabulary both services share (Step 3): the API adds
connection jobs by these names and the scheduler runs them, so neither imports the
other. Step 4 adds `issue_text()`; every outbound rendering must come back here.
"""

from __future__ import annotations

import re
from typing import Final

# What an issue or email says when the name itself must not leave (D48).
A_SERVICE: Final = "a service"

# D39: long enough for real names, short enough that a name is not a payload.
STRICT_NAME_LENGTH: Final = 64

# A run of 6 or more digits reads as an id or a phone number, a guess (FR37).
_LOOKS_PERSONAL: Final = re.compile(r".@.|\d{6}")
_OUTSIDE_STRICT: Final = re.compile(r"[^A-Za-z0-9_/\-]")


def strict_name(service_name: str | None) -> str:
    """A service name as it may leave this server, or the fixed words."""
    if service_name is None or _LOOKS_PERSONAL.search(service_name):
        return A_SERVICE
    cleaned = _OUTSIDE_STRICT.sub("_", service_name)[:STRICT_NAME_LENGTH]
    return cleaned if cleaned.strip("_/") else A_SERVICE


# ------------------------------------------------------------- queues and tasks

# F5's layout (its D40): sync on a capped queue, person-started work beside it, so a
# hung tracker can never take the last slot from a test or a sweep (AC86).
CONNECTIONS_QUEUE: Final = "connections"
CONNECTIONS_INTERACTIVE_QUEUE: Final = "connections-interactive"
CONNECTIONS_QUEUE_CAP: Final = 2
# `scheduler-connections`' slots; the cap above leaves one for the interactive queue.
CONNECTIONS_CONCURRENCY: Final = 3

# Turns the manifest code GitHub returned into the App's key and webhook secret.
CONVERT_MANIFEST_TASK: Final = "nexovue_scheduler.connections.convert_manifest"
# Binds the App's own installation and reads the repositories it reaches.
REFRESH_CONNECTION_TASK: Final = "nexovue_scheduler.connections.refresh"
# "Test this connection" (FR30), and the first test after setup or a new key.
CHECK_CONNECTION_TASK: Final = "nexovue_scheduler.connections.check"
CONNECTION_TASKS: Final = (CONVERT_MANIFEST_TASK, REFRESH_CONNECTION_TASK, CHECK_CONNECTION_TASK)

# What a connection test leaves in `job_results`, and how long it waits to be read.
CONNECTION_TEST_RESULT_KIND: Final = "connection_test"
CONNECTION_TEST_RESULT_TTL_SECONDS: Final = 300


def connection_job_arguments(*, org_id: str, connection_id: str) -> dict[str, str]:
    """Ids only (AC74): the job opens what it needs, and its first read binds both (AC129)."""
    return {"org_id": org_id, "connection_id": connection_id}


def connection_test_arguments(
    *, org_id: str, connection_id: str, request_id: str
) -> dict[str, str]:
    return {
        **connection_job_arguments(org_id=org_id, connection_id=connection_id),
        "request_id": request_id,
    }


# -------------------------------------------------------------------- the words

# `github_fix` is F5's own App (F5 D36); F22's sweeps ignore it.
CONNECTION_KINDS: Final = ("github", "jira", "github_fix")
TRACKER_KINDS: Final = ("github", "jira")
GITHUB: Final = "github"

CONNECTION_STATES: Final = ("setting_up", "working", "failing", "needs_attention", "disconnected")
SETTING_UP: Final = "setting_up"
WORKING: Final = "working"
FAILING: Final = "failing"
NEEDS_ATTENTION: Final = "needs_attention"
DISCONNECTED: Final = "disconnected"
LIVE_STATES: Final = tuple(state for state in CONNECTION_STATES if state != DISCONNECTED)

# A synced or reference link's status (spec, *Link and connection states*); the table
# that holds them arrives with Step 4 and freezes this list in its migration.
LINK_STATUSES: Final = (
    "creating",
    "promoting",
    "in_sync",
    "waiting",
    "needs_attention",
    "refused",
    "gone",
    "not_followed",
    "stopped",
)

# Why a connection or a setup last failed. An enum and never a provider's text, which
# is outside text (design, *Failure handling*); the console words each by reader.
CONNECTION_FAILURES: Final = (
    "unreachable",
    "timed_out",
    "server_error",
    "rate_limited",
    "unauthorized",
    "app_not_installed",
    "installation_ambiguous",
    "no_repository",
    "key_unreadable",
    "manifest_expired",
    "manifest_refused",
    "unexpected_answer",
)

# A failing connection becomes `failing` after this many straight failures (AC79).
FAILURES_BEFORE_FAILING: Final = 5

# ------------------------------------------------------------------- sealing

# Separate purposes under `CONNECTION_SECRET_KEY`, each bound to its connection, so
# a value copied into another row or column fails to open.
CONNECTION_CREDENTIAL_PURPOSE: Final = "connection-credential"
CONNECTION_WEBHOOK_SECRET_PURPOSE: Final = "connection-webhook-secret"
CONNECTION_MANIFEST_CODE_PURPOSE: Final = "connection-manifest-code"


def connection_secret_owner(org_id: str, connection_id: str) -> str:
    return f"{org_id}:{connection_id}"


# ------------------------------------------------------------ built addresses

# GitHub's owner: letters, digits and inner hyphens, at most 39. A repository name:
# letters, digits, `.`, `_` and `-`, never `.` or `..` alone (general knowledge of
# GitHub's rules, not read from its pages).
_OWNER: Final = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPOSITORY: Final = r"(?!\.{1,2}(?:#|$))[A-Za-z0-9._-]{1,100}"
_REPOSITORY_KEY: Final = re.compile(rf"{_OWNER}/{_REPOSITORY}")
_GITHUB_ISSUE_KEY: Final = re.compile(
    rf"(?P<repository>{_OWNER}/{_REPOSITORY})#(?P<number>[1-9][0-9]{{0,9}})"
)
# FR40 and D26.
_JIRA_ISSUE_KEY: Final = re.compile(r"[A-Z][A-Z0-9_]+-[0-9]+")
_JIRA_SITE: Final = re.compile(r"[a-z0-9][a-z0-9-]*\.atlassian\.net")


# What GitHub's manifest redirect hands back as `code`. It becomes part of a GitHub path
# in the conversion job, so only a plain token is accepted (a shape chosen here, not
# read from GitHub: its codes seen in documentation are short hexadecimal strings).
_MANIFEST_CODE: Final = re.compile(r"[A-Za-z0-9_-]{1,100}")


def is_github_account(name: str) -> bool:
    """Whether `name` is a GitHub user or organization login."""
    return re.fullmatch(_OWNER, name) is not None


def is_manifest_code(code: str) -> bool:
    return _MANIFEST_CODE.fullmatch(code) is not None


def is_repository_key(key: str) -> bool:
    """Whether `key` is a GitHub `owner/name`, the only form a place's key is stored in."""
    return _REPOSITORY_KEY.fullmatch(key) is not None


def link_url(kind: str, site: str | None, external_key: str) -> str:
    """The issue's address, built here from its strict key (D81): `finding_links` stores
    no address, so one an outside system chose can never reach a colleague's click."""
    if kind == GITHUB:
        issue = _GITHUB_ISSUE_KEY.fullmatch(external_key)
        if issue is None:
            raise ValueError("the key is not a GitHub issue key")
        return f"https://github.com/{issue['repository']}/issues/{issue['number']}"
    if kind == "jira":
        if site is None or _JIRA_SITE.fullmatch(site) is None:
            raise ValueError("the site is not an atlassian.net site")
        if _JIRA_ISSUE_KEY.fullmatch(external_key) is None:
            raise ValueError("the key is not a Jira issue key")
        return f"https://{site}/browse/{external_key}"
    raise ValueError(f"a {kind} connection does not hold issues")
