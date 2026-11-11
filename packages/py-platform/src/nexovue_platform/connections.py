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
other. Step 4 adds `issue_text()` and the fixed comments, the one place an issue's
words are made: the API renders the preview with it and the sync job the issue, from
the same frozen facts (AC83).
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from nexovue_platform.alerts.model import SEVERITIES
from nexovue_platform.detection import SIGNALS
from nexovue_platform.jobs import JobRequest

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
# Reads, compares and writes one connection's due links (design, *The job*).
SYNC_CONNECTION_TASK: Final = "nexovue_scheduler.connections.sync"
# Beat's, every 30 seconds while a tracker connection is live: one sync job per
# connection with a due link, and each connection's hourly repository refresh.
SWEEP_CONNECTIONS_TASK: Final = "nexovue_scheduler.connections.sweep"
CONNECTION_TASKS: Final = (
    CONVERT_MANIFEST_TASK,
    REFRESH_CONNECTION_TASK,
    CHECK_CONNECTION_TASK,
    SYNC_CONNECTION_TASK,
    SWEEP_CONNECTIONS_TASK,
)

# A sync job not started within this is dropped: the next sweep adds another (design).
SYNC_JOB_EXPIRES_SECONDS: Final = 60
# Its limits, a guess: a run that reaches the soft one stops cleanly, the rest stay due.
SYNC_SOFT_LIMIT_SECONDS: Final = 240
SYNC_HARD_LIMIT_SECONDS: Final = 270

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

# A synced or reference link's status (spec, *Link and connection states*), frozen by
# the `finding_links` migration.
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

# A link's own failure: a connection's, plus what one issue or repository can do. An
# enum and never a provider's text; the console words each by reader (FR44).
LINK_FAILURES: Final = (
    "unreachable",
    "timed_out",
    "server_error",
    "rate_limited",
    "unauthorized",
    "unexpected_answer",
    # The repository answers nothing to this App any more (design, row 10).
    "repository_unreachable",
    # A transfer took the issue to a repository this connection does not reach (row 11).
    "moved_out_of_reach",
    # Reopened in the tracker while another case is active on the service (row 7).
    "reopen_blocked",
    # The tracker refused a status write or comment for a lasting reason.
    "write_refused",
    # BR12: a public repository nobody allowed.
    "public_repository",
)
LINK_ROLES: Final = ("synced", "reference")
SYNCED: Final = "synced"
# Any other issue attached to a case: shown, read hourly, and never changing it (FR41).
REFERENCE: Final = "reference"
# What a tracker's issue is, as nexovue stores it: open or closed, nothing more (BR10).
ISSUE_STATES: Final = ("open", "closed")
CREATING: Final = "creating"
# A person chose to sync a reference; the job's public check and first read decide (FR41).
PROMOTING: Final = "promoting"
IN_SYNC: Final = "in_sync"
STOPPED: Final = "stopped"
NOT_FOLLOWED: Final = "not_followed"

# The poll schedule (D49), one function of the case's state: guesses all.
ACTIVE_POLL: Final = timedelta(minutes=5)
RESOLVED_POLL: Final = timedelta(hours=1)
# A resolved case's link is followed this long, then `not_followed` (FR39, AC85).
FOLLOW_AFTER_RESOLVE: Final = timedelta(days=30)
# A reference link is read hourly whatever its case's state (FR41, a guess), and stops
# 30 days after its case resolved, as a synced one does.
REFERENCE_POLL: Final = timedelta(hours=1)
# A 404 from a repository still readable is read again daily for a week (row 9).
GONE_RECHECK: Final = timedelta(days=1)
GONE_RECHECK_FOR: Final = timedelta(days=7)
# Each connection's repositories are read again hourly (FR42), by `sweep_connections`.
REPOSITORY_REFRESH: Final = timedelta(hours=1)
# A refusal once this many issues are being created on one connection (spec, a guess).
MAX_CREATING_PER_CONNECTION: Final = 20
# Reference links a case may hold (spec, *Packaging and limits*, a guess).
MAX_REFERENCES_PER_CASE: Final = 20


def sync_job(*, org_id: str, connection_id: str) -> JobRequest:
    """One `sync_connection` job, the same from a click and from the sweep, so the
    conditional insert (`jobs.ENQUEUE_UNLESS_WAITING`) sees a waiting one (AC48)."""
    return JobRequest(
        task=SYNC_CONNECTION_TASK,
        queue=CONNECTIONS_QUEUE,
        kwargs=connection_job_arguments(org_id=org_id, connection_id=connection_id),
        org_id=org_id,
        expires_seconds=SYNC_JOB_EXPIRES_SECONDS,
        soft_limit_seconds=SYNC_SOFT_LIMIT_SECONDS,
        hard_limit_seconds=SYNC_HARD_LIMIT_SECONDS,
    )


def refresh_job(*, org_id: str, connection_id: str) -> JobRequest:
    """One repository refresh, as the hourly sweep adds it."""
    return JobRequest(
        task=REFRESH_CONNECTION_TASK,
        queue=CONNECTIONS_INTERACTIVE_QUEUE,
        kwargs=connection_job_arguments(org_id=org_id, connection_id=connection_id),
        org_id=org_id,
        expires_seconds=SYNC_JOB_EXPIRES_SECONDS,
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
# letters, digits, `.`, `_` and `-`, never `.` or `..` alone, in a key or an address path
# (general knowledge of GitHub's rules, not read from its pages).
_OWNER: Final = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPOSITORY: Final = r"(?!\.{1,2}(?:[#/]|$))[A-Za-z0-9._-]{1,100}"
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


def is_issue_key(key: str) -> bool:
    """Whether `key` is a GitHub `owner/name#number` or a Jira key, the only forms an
    issue is stored by (FR40)."""
    return (
        _GITHUB_ISSUE_KEY.fullmatch(key) is not None or _JIRA_ISSUE_KEY.fullmatch(key) is not None
    )


def github_issue_key(repository: str, number: int) -> str | None:
    """`owner/name#number`, or None when either part is outside the strict forms."""
    key = f"{repository}#{number}"
    return key if _GITHUB_ISSUE_KEY.fullmatch(key) is not None else None


def split_github_issue_key(key: str) -> tuple[str, int]:
    issue = _GITHUB_ISSUE_KEY.fullmatch(key)
    if issue is None:
        raise ValueError("the key is not a GitHub issue key")
    return issue["repository"], int(issue["number"])


# A pasted address is a few dozen characters; anything far longer is not one (FR40).
MAX_PASTED_ADDRESS_LENGTH: Final = 2048
# GitHub's issue page, `https://github.com/<owner>/<name>/issues/<n>`, with any trailing
# slash, query or fragment (`#issuecomment-1`) ignored; or the key itself, `owner/name#n`.
# Pull requests and every other page are not issues and are refused.
_GITHUB_ISSUE_ADDRESS: Final = re.compile(
    rf"https?://(?:www\.)?(?i:github\.com)/(?P<repository>{_OWNER}/{_REPOSITORY})"
    r"/issues/(?P<number>[1-9][0-9]{0,9})/?(?:[?#].*)?"
)


@dataclass(frozen=True, slots=True)
class PastedIssue:
    """An issue a person pasted, parsed to its parts; nothing was fetched (FR40)."""

    kind: str
    # The repository as pasted. GitHub's names ignore case, so the connected place it
    # names is found ignoring case, and the stored key takes that place's own spelling.
    place_key: str
    number: int

    def key_in(self, place_key: str) -> str:
        """The issue's strict key in the connected place's own spelling."""
        key = github_issue_key(place_key, self.number)
        if key is None:
            raise ValueError("the place's key is not a GitHub repository key")
        return key


def parse_issue_address(address: str) -> PastedIssue | None:
    """A pasted GitHub issue address or key, or None when it is neither (FR40). Jira's
    `/browse/KEY-1` and bare keys join here with Step 7 (foundation item 29)."""
    pasted = address.strip()
    if len(pasted) > MAX_PASTED_ADDRESS_LENGTH:
        return None
    found = _GITHUB_ISSUE_ADDRESS.fullmatch(pasted) or _GITHUB_ISSUE_KEY.fullmatch(pasted)
    if found is None:
        return None
    return PastedIssue(kind=GITHUB, place_key=found["repository"], number=int(found["number"]))


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


# ------------------------------------------------------------------ issue text
# Design, *Issue text: outbound escaping*. Facts are a typed value holding numbers,
# enums, the strict name and nexovue's own link back: there is no field a title, a
# note, an endpoint or a model's words could arrive by (AC42, D48). A preview and the
# issue the job writes render the same frozen facts, so their bytes match (AC83).

_SIGNAL_TITLE: Final = {
    "slower": "slower than normal",
    "failing": "failing more than normal",
    "traffic": "losing traffic",
}
# What moved, and the pronoun its range takes: "errors" are plural.
_SIGNAL_MEASURE: Final = {
    "slower": ("latency", "its"),
    "failing": ("errors", "their"),
    "traffic": ("traffic", "its"),
}
# A ratio at or past this reads as a whole number: "25x", not "25.0x".
_WHOLE_RATIO_FROM: Final = 10
_LINK_BACK: Final = re.compile(r"https?://[^\s<>`\[\]()]+/findings/(?P<finding>[0-9a-f-]{36})")
_FACT_FIELDS: Final = frozenset(
    {"finding_id", "signal", "ratio", "severity", "since", "service", "link_back"}
)
SIGNIN_LINE: Final = "This link needs a nexovue account. Ask your admin for an invitation."
ISSUE_LABEL: Final = "nexovue"


@dataclass(frozen=True, slots=True)
class IssueFacts:
    """What an issue may say. Frozen on the link at the click (`frozen_facts`)."""

    finding_id: str
    signal: str
    # The leading signal's current reading over its normal; None when either is missing.
    ratio: float | None
    severity: str
    since: datetime
    # Already in strict form, or the fixed words `a service`.
    service: str
    # nexovue's own address for the finding, built from `APP_BASE_URL`.
    link_back: str

    def __post_init__(self) -> None:
        if self.signal not in SIGNALS:
            raise ValueError(f"signal must be one of {SIGNALS}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        if self.ratio is not None and (not math.isfinite(self.ratio) or self.ratio < 0):
            raise ValueError("ratio must be a finite, non-negative number")
        if self.service != A_SERVICE and strict_name(self.service) != self.service:
            raise ValueError("service must be in strict form")
        link = _LINK_BACK.fullmatch(self.link_back)
        if link is None or link["finding"] != self.finding_id:
            raise ValueError("link_back must be nexovue's address for this finding")

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"since": self.since.astimezone(UTC).isoformat()}

    @classmethod
    def from_json(cls, frozen: dict[str, Any]) -> IssueFacts:
        """The copy frozen at the click, checked again: it is a stored value, and a
        tampered one must not reach a tracker."""
        if set(frozen) != _FACT_FIELDS:
            raise ValueError("frozen facts hold exactly the issue's fields")
        try:
            since = datetime.fromisoformat(frozen["since"])
            ratio = frozen["ratio"]
            return cls(
                finding_id=str(uuid.UUID(str(frozen["finding_id"]))),
                signal=frozen["signal"],
                ratio=None if ratio is None else float(ratio),
                severity=frozen["severity"],
                since=since,
                service=frozen["service"],
                link_back=frozen["link_back"],
            )
        except (TypeError, KeyError, AttributeError) as unreadable:
            raise ValueError("frozen facts are not readable") from unreadable


def finding_link_back(base_url: str, finding_id: uuid.UUID | str) -> str:
    return f"{base_url.rstrip('/')}/findings/{finding_id}"


def issue_facts(
    *,
    finding_id: uuid.UUID | str,
    signal: str,
    current: float | None,
    normal: float | None,
    severity: str,
    since: datetime,
    service_name: str | None,
    base_url: str,
) -> IssueFacts:
    """A finding's facts as an issue may carry them; the only way to build one."""
    ratio = None if current is None or not normal else current / normal
    return IssueFacts(
        finding_id=str(uuid.UUID(str(finding_id))),
        signal=signal,
        ratio=ratio,
        severity=severity,
        since=since,
        service=strict_name(service_name),
        link_back=finding_link_back(base_url, finding_id),
    )


@dataclass(frozen=True, slots=True)
class IssueText:
    """One issue in each tracker's form: GitHub's Markdown, Jira's ADF."""

    title: str
    markdown: str
    adf: dict[str, Any]


def _times(ratio: float) -> str:
    if ratio >= _WHOLE_RATIO_FROM:
        return f"{round(ratio)}x"
    return f"{round(ratio, 1):g}x"


def _what_moved(facts: IssueFacts) -> str:
    measure, its = _SIGNAL_MEASURE[facts.signal]
    if facts.signal == "traffic":
        if facts.ratio is None:
            return f"{measure}, below {its} normal range"
        return f"{measure}, {round(facts.ratio * 100)}% of {its} normal range"
    if facts.ratio is None:
        return f"{measure}, above {its} normal range"
    return f"{measure}, {_times(facts.ratio)} {its} normal range"


def _utc_minute(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _paragraph(*parts: tuple[str, bool]) -> dict[str, Any]:
    """An ADF paragraph of plain text nodes; a part marked True carries the code mark."""
    return {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": words, **({"marks": [{"type": "code"}]} if code else {})}
            for words, code in parts
            if words
        ],
    }


def _adf(paragraphs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": paragraphs}


def issue_text(facts: IssueFacts) -> IssueText:
    """The issue, from fixed sentences and numbers alone. The strict name sits in a
    code span (GitHub) or a code mark (Jira); a title has no code span, so `GH-12` or a
    hexadecimal name could still link there, which T4.8 renders against GitHub."""
    named = facts.service != A_SERVICE
    lines = [
        f"What moved: {_what_moved(facts)}",
        f"Since: {_utc_minute(facts.since)}",
        f"Severity: {facts.severity}",
    ]
    link_lines = [f"Evidence, and the case: {facts.link_back}", SIGNIN_LINE]
    on = f"`{facts.service}`" if named else facts.service
    markdown = (
        f"nexovue detected this on {on}.\n\n"
        + "".join(f"- {line}\n" for line in lines)
        + "\n"
        + "".join(f"{line}\n" for line in link_lines)
    )
    opening = _paragraph(("nexovue detected this on ", False), (facts.service, named), (".", False))
    adf = _adf(
        [opening, *(_paragraph((line, False)) for line in [*lines, *link_lines])],
    )
    return IssueText(
        title=f"nexovue: {_SIGNAL_TITLE[facts.signal]} on {facts.service}",
        markdown=markdown,
        adf=adf,
    )


# ------------------------------------------------------------------- comments
# BR10: one fixed sentence per value of `finding_links.comment_wanted`, naming nobody.

COMMENT_KINDS: Final = ("resolved", "false_alarm", "new_normal", "idle_warning")
IDLE_WARNING: Final = "idle_warning"
_COMMENTS: Final = {
    "resolved": "The nexovue case for this issue was resolved, so nexovue closed it.",
    "false_alarm": "The nexovue case for this issue ended as a false alarm, so nexovue closed it.",
    "new_normal": "The nexovue case for this issue ended as the new normal, so nexovue closed it.",
    "idle_warning": (
        "nexovue will end its case for this issue on {ends_on} if nobody acts in nexovue. "
        "The issue stays open."
    ),
}


@dataclass(frozen=True, slots=True)
class CommentText:
    markdown: str
    adf: dict[str, Any]


def comment_text(kind: str, *, ends_on: date | None) -> CommentText:
    if kind not in _COMMENTS:
        raise ValueError(f"comment must be one of {COMMENT_KINDS}")
    if kind == IDLE_WARNING and ends_on is None:
        raise ValueError("the idle warning names the date the case ends")
    words = _COMMENTS[kind].format(
        ends_on="" if ends_on is None else f"{ends_on.day} {ends_on:%b %Y}"
    )
    return CommentText(markdown=words, adf=_adf([_paragraph((words, False))]))


# The comment a person's ending owes with its close (FR38's table): a resolve and a new
# normal close as completed, a false alarm as not planned.
COMMENT_FOR_RESOLUTION: Final = {
    "resolved": "resolved",
    "false_alarm": "false_alarm",
    "new_normal": "new_normal",
}


def close_reason_for(comment_wanted: str | None) -> str:
    """GitHub's `state_reason` for nexovue's own close."""
    return "not_planned" if comment_wanted == "false_alarm" else "completed"
