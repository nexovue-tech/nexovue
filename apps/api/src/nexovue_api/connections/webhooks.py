"""What each tracker kind's webhook looks like (F22 T5.1; design, *Webhook handling*).

A webhook only rings the doorbell (ADR-035): once its signature holds, the one thing read
from its body is which issue it is about, so that issue's links are read sooner. Nothing
else in the body is trusted or kept.

One form per kind, chosen from `connections.kind`, never from the request. A kind with no
form here is refused like a bad signature. Jira adds its form with Step 7: it signs with
`X-Hub-Signature: sha256=…` and names `issue.id` as a string (design, *Connecting Jira*).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from nexovue_platform.connections import GITHUB

# `finding_links.external_id`'s own check (migration 0061): digits, at most 20.
_ISSUE_ID: Final = re.compile(r"[0-9]{1,20}")


@dataclass(frozen=True)
class WebhookForm:
    """Where a kind's signature is, and how its body names an issue."""

    signature_header: str
    issue_id_of: Callable[[Mapping[str, Any]], str | None]


def _github_issue_id(document: Mapping[str, Any]) -> str | None:
    """`issue.id`, the number the sync stores as `external_id` from GitHub's REST answer.

    Unverified against GitHub: that an `issues` delivery carries the REST issue's `id` was
    read from general knowledge and not confirmed on GitHub's payload page, which could not
    be reached. T5.2's real delivery confirms it. A delivery without it rings nothing, and
    polling still follows the issue.
    """
    issue = document.get("issue")
    identifier = issue.get("id") if isinstance(issue, dict) else None
    # `bool` is an `int` in Python; `true` is not an issue.
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        return None
    text = str(identifier)
    return text if _ISSUE_ID.fullmatch(text) else None


WEBHOOK_FORMS: Final[Mapping[str, WebhookForm]] = {
    # GitHub's HMAC-SHA256 header; the SHA-1 `X-Hub-Signature` it also sends is not read.
    GITHUB: WebhookForm(signature_header="X-Hub-Signature-256", issue_id_of=_github_issue_id),
}


def issue_id_in(form: WebhookForm, body: bytes) -> str | None:
    """The issue a signed body is about, or None for any other shape (a ping, say)."""
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return form.issue_id_of(document) if isinstance(document, dict) else None
