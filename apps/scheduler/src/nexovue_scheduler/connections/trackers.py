"""The per-kind boundary (F22, design *Connections*: *The seam*).

What sync and setup ask of a tracker, whichever it is. Provider differences stop inside
an adapter, as the foundation's AI interface does, so the jobs in `tasks.py` hold no
GitHub or Jira knowledge. Step 3 needs only what setup and the connection test ask:
`check` and `reach`. Step 4 adds reading, creating, writing a status and commenting.

A base address is a constructor argument only (AC89): `tracker_for` passes the module's
constant, and no setting or environment variable can move it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from nexovue_platform.connections import GITHUB


class TrackerFailure(Exception):  # noqa: N818 — named for what it reports
    """A call failed. `failure` is one of `CONNECTION_FAILURES`; never a provider's text."""

    def __init__(self, failure: str, status: int | None = None) -> None:
        super().__init__(failure if status is None else f"{failure} ({status})")
        self.failure = failure
        self.status = status


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


class Tracker(Protocol):
    def check(self) -> None:
        """Prove the stored credential still works; raise `TrackerFailure` if not."""

    def reach(self) -> Reach:
        """Read what the connection reaches; raise `TrackerFailure` if it cannot."""


@dataclass(frozen=True, slots=True)
class StoredConnection:
    """What an adapter is built from: the row's own facts and its opened credential."""

    kind: str
    credential: str
    github_app_id: int | None
    github_installation_id: int | None


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
