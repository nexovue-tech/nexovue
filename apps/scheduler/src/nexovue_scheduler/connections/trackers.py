"""The per-kind boundary (F22, design *Connections*: *The seam*).

What sync and setup ask of a tracker, whichever it is. Provider differences stop inside
an adapter, as the foundation's AI interface does, so the jobs in `tasks.py` and
`sync.py` hold no GitHub or Jira knowledge: setup and the test ask `check` and `reach`;
sync asks `read`, `find_existing`, `create`, `set_status`, `comment` and `is_public`, in
the design's own words, and `finish` lets an adapter revoke what a run minted.

A base address is a constructor argument only (AC89): `tracker_for` passes the module's
constant, and no setting or environment variable can move it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from nexovue_platform.connections import GITHUB, CommentText, IssueText


class TrackerFailure(Exception):  # noqa: N818 — named for what it reports
    """A call failed. `failure` is one of `LINK_FAILURES`, which hold `CONNECTION_FAILURES`'
    words a connection can store too; never a provider's text."""

    def __init__(
        self, failure: str, status: int | None = None, *, retry_after: float | None = None
    ) -> None:
        super().__init__(failure if status is None else f"{failure} ({status})")
        self.failure = failure
        self.status = status
        # Seconds the tracker asked to be left alone, on a rate limit (AC49).
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Place:
    """A repository or project a connection reaches, by its strict key."""

    external_id: str
    key: str
    is_private: bool


@dataclass(frozen=True, slots=True)
class Reach:
    """What a connection reaches now. `github_installation_id` is the App's own, read
    from GitHub, never from a request (AC38)."""

    places: tuple[Place, ...]
    github_installation_id: int | None = None


@dataclass(frozen=True, slots=True)
class PlaceKey:
    """Where an issue lives: a repository or project, by its id and strict key."""

    external_id: str
    key: str


@dataclass(frozen=True, slots=True)
class IssueRef:
    """An issue by the tracker's own id and its strict key (`owner/name#n`, `OPS-12`)."""

    external_id: str
    external_key: str


@dataclass(frozen=True, slots=True)
class IssueSeen:
    state: str
    # The tracker's close reason, as its enum (`ISSUE_CLOSE_REASONS`); None when open.
    close_reason: str | None
    # The tracker's own id for the issue, under `external_id`'s strict form; a pasted
    # reference learns it from its first read (FR40), and a webhook rings by it.
    issue_id: str | None = None


@dataclass(frozen=True, slots=True)
class IssueGone:
    """The tracker says the issue was deleted (GitHub: 410)."""


@dataclass(frozen=True, slots=True)
class IssueMissing:
    """Not found; whether its repository still answers decides rows 9 and 10."""

    repository_readable: bool


@dataclass(frozen=True, slots=True)
class IssueMoved:
    """Moved elsewhere; where, if the target could be read under the strict forms."""

    repository: str | None
    number: int | None
    issue_id: str | None


IssueRead = IssueSeen | IssueGone | IssueMissing | IssueMoved


class Tracker(Protocol):
    # How long after a create its search is believed to see it (design, *Creating
    # without duplicates*): a retry looks only once this has passed.
    search_settles_seconds: float

    def check(self) -> None:
        """Prove the stored credential still works; raise `TrackerFailure` if not."""

    def reach(self) -> Reach:
        """Read what the connection reaches; raise `TrackerFailure` if it cannot."""

    def read(self, place: PlaceKey, issue: IssueRef) -> IssueRead:
        """The issue's status now, or that it is gone, missing or moved."""

    def find_existing(self, place: PlaceKey, link_back: str, since: datetime) -> IssueRef | None:
        """An issue this connection created since `since` whose body holds `link_back`."""

    def create(self, place: PlaceKey, text: IssueText) -> IssueRef:
        """Write a new issue; raise `TrackerFailure` if it was not written."""

    def set_status(self, place: PlaceKey, issue: IssueRef, wanted: str, close_reason: str) -> None:
        """Open or close the issue; `close_reason` is GitHub's `state_reason` for a close."""

    def comment(self, place: PlaceKey, issue: IssueRef, text: CommentText) -> None:
        """Add one fixed comment."""

    def is_public(self, place: PlaceKey) -> bool:
        """Whether anyone can read the place now (BR12); raise if it cannot be told."""

    def finish(self) -> None:
        """End a run: revoke what it minted, best effort."""


@dataclass(frozen=True, slots=True)
class StoredConnection:
    """What an adapter is built from: the row's own facts and its opened credential."""

    kind: str
    credential: str
    github_app_id: int | None
    github_installation_id: int | None
    github_app_slug: str | None = None


def tracker_for(connection: StoredConnection, *, private_allowed: bool) -> Tracker:
    """The adapter for a connection's kind, at its provider's own address."""
    # Imported here so the boundary names no adapter's dependencies at import.
    from nexovue_scheduler.connections import github

    if connection.kind == GITHUB:
        return github.GitHubTracker.for_connection(
            connection,
            github.GitHubEndpoint(base_url=github.GITHUB_API, private_allowed=private_allowed),
        )
    # Jira's adapter waits on Atlassian's written answer (F22 Step 7, foundation item 29).
    raise ValueError(f"no adapter is built for a {connection.kind} connection")
