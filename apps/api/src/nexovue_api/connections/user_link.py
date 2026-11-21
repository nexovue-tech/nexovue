"""Linking a person's GitHub account (F5 design, *GitHub account link*; plan T3.17; A18).

So GitHub can say what access a person has to a repository, under an owner's requirement
(D20) and, later, before a draft (D6). **No token is stored:** the job exchanges GitHub's
one-time code, keeps the GitHub user id and login, and drops the token in memory.

- `start` writes an attempt with the SHA-256 of a random `state` and of the session that
  asked, and answers GitHub's authorize address. The raw `state` goes only to the browser.
- GitHub returns the browser to a console route, which **posts** `code` and `state` to
  `complete` (a `GET` callback would escape the support view's read-only rule). The
  attempt must be this person's, this session's, unused and inside its ten minutes; the
  code is sealed onto the attempt under `CONNECTION_SECRET_KEY` and its job added with
  ids only, so no job row ever holds a code (AC15). Another person's session is refused,
  so one Nexovue account cannot be tied to another's GitHub identity (A18).
- The console polls the attempt. `DELETE` removes a person's own link.

Audit details name the Nexovue person only, never a GitHub login or id (AC31).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Final
from urllib.parse import urlencode

from nexovue_platform.checks.sealing import ConfiguredKeys
from nexovue_platform.fixes.records import (
    GITHUB_LINK_CODE_PURPOSE,
    LINK_ATTEMPT_LIFETIME,
    LINK_RUNNING,
    LINK_WAITING,
    complete_github_user_link_request,
    github_link_code_owner,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.auth.sessions import Session
from nexovue_api.connections import jobs, queries
from nexovue_api.connections.fix_app import FIX_APP_KIND, LINK_CALLBACK_PATH
from nexovue_api.connections.fix_schemas import (
    GitHubLinkAttemptView,
    GitHubLinkStarted,
    GitHubLinkView,
)
from nexovue_api.connections.service import sealed_box
from nexovue_api.data.postgres.models import GitHubLinkAttempt, GitHubUserLink
from nexovue_api.errors.exceptions import ConflictError, InvalidRequestError, NotFoundError

AUTHORIZE_URL: Final = "https://github.com/login/oauth/authorize"
# GitHub's OAuth code is short and plain (general knowledge; unverified until the trial).
CODE_MAX_CHARACTERS: Final = 100
_NO_APP: Final = "The Nexovue suggestions App is not set up, so no account can be linked yet."
_INVALID: Final = "This link is not valid. Nothing was changed."
_NO_SUCH_ATTEMPT: Final = "No such link attempt."


def session_hash(session: Session) -> str:
    """Which session started an attempt: its person, organization and when it began."""
    marker = f"{session.user_id}:{session.org_id}:{session.created_at.isoformat()}"
    return hashlib.sha256(marker.encode()).hexdigest()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def start(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Session,
    app_base_url: str,
    now: datetime,
) -> GitHubLinkStarted:
    app = await queries.live_github_connection(db, org_id=org_id, kind=FIX_APP_KIND)
    if app is None or app.github_client_id is None:
        raise ConflictError(_NO_APP)
    state = secrets.token_urlsafe(32)
    attempt = GitHubLinkAttempt(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=user_id,
        session_hash=session_hash(session),
        state_hash=_hash(state),
        status=LINK_WAITING,
        created_at=now,
        expires_at=now + LINK_ATTEMPT_LIFETIME,
    )
    db.add(attempt)
    query = urlencode(
        {
            "client_id": app.github_client_id,
            "state": state,
            "redirect_uri": app_base_url.rstrip("/") + LINK_CALLBACK_PATH,
            "allow_signup": "false",
        }
    )
    return GitHubLinkStarted(attempt_id=attempt.id, authorize_url=f"{AUTHORIZE_URL}?{query}")


async def complete(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Session,
    state: str,
    code: str,
    keys: ConfiguredKeys,
    now: datetime,
) -> GitHubLinkAttemptView:
    """Take GitHub's redirect back: only this person, this session, once, in time."""
    if not code or len(code) > CODE_MAX_CHARACTERS or not code.isascii() or not code.strip():
        raise InvalidRequestError(_INVALID)
    attempt = await db.scalar(
        select(GitHubLinkAttempt)
        .where(
            GitHubLinkAttempt.org_id == org_id,
            GitHubLinkAttempt.state_hash == _hash(state),
            GitHubLinkAttempt.status == LINK_WAITING,
        )
        .with_for_update()
    )
    if (
        attempt is None
        or attempt.user_id != user_id
        or attempt.session_hash != session_hash(session)
        or attempt.expires_at <= now
    ):
        raise InvalidRequestError(_INVALID)
    attempt.status = LINK_RUNNING
    attempt.sealed_code = sealed_box(keys, GITHUB_LINK_CODE_PURPOSE).seal(
        code, owner=github_link_code_owner(str(org_id), str(user_id), str(attempt.id))
    )
    await jobs.add(db, complete_github_user_link_request(attempt_id=attempt.id, org_id=org_id))
    return _attempt_view(attempt)


async def attempt_of(
    db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, attempt_id: uuid.UUID
) -> GitHubLinkAttemptView:
    """A person's own attempt; anyone else's is a 404 like none."""
    attempt = await db.scalar(
        select(GitHubLinkAttempt).where(
            GitHubLinkAttempt.org_id == org_id,
            GitHubLinkAttempt.user_id == user_id,
            GitHubLinkAttempt.id == attempt_id,
        )
    )
    if attempt is None:
        raise NotFoundError(_NO_SUCH_ATTEMPT)
    return _attempt_view(attempt)


def _attempt_view(attempt: GitHubLinkAttempt) -> GitHubLinkAttemptView:
    return GitHubLinkAttemptView(id=attempt.id, status=attempt.status, reason=attempt.reason)


async def link_of(db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> GitHubLinkView:
    link = await db.scalar(
        select(GitHubUserLink).where(
            GitHubUserLink.org_id == org_id, GitHubUserLink.user_id == user_id
        )
    )
    if link is None:
        return GitHubLinkView(linked=False, login=None, linked_at=None)
    return GitHubLinkView(linked=True, login=link.github_login, linked_at=link.linked_at)


async def unlink(db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """A person removes their own link; audited by the Nexovue person alone."""
    removed = await db.execute(
        delete(GitHubUserLink)
        .where(GitHubUserLink.org_id == org_id, GitHubUserLink.user_id == user_id)
        .returning(GitHubUserLink.user_id)
    )
    if removed.first() is not None:
        audit.record_fix_event(
            db,
            org_id=org_id,
            actor_user_id=user_id,
            action=audit.GITHUB_ACCOUNT_UNLINKED,
            target_kind=audit.USER_TARGET,
            target_id=user_id,
        )
