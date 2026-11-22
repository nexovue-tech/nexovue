"""What a draft pull request says, all of it a template (spec BR10, BR11, FR23, FR24a, FR26,
AC27, AC62; design, *The draft click*, commit message and pull request body; plan T7.6).

**No model prose reaches GitHub** (U5): the title, the commit message and the body are
Nexovue's fixed words filled with Nexovue's own values: the folder from the customer's code
location, file paths from the repository's own tree with a fixed reason, flags by fixed
code and line. The only free text is the requester's one line (FR24a), checked by FR16's
prose checks before it is stored, again here, and shown inside a code span, so no link,
mention or markup in it is followed.

**A public repository** (A10) gets none of the telemetry figures, the console link or the
GitHub login, only the requester's Nexovue role, and the note only when they confirmed it.
**A private one** gets the signal and its numbers, the console link, and the requester's
GitHub login as plain text, with no `@`, so nobody is notified.

Pure: no store, no clock, no network.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from nexovue_platform.fixes.prose import prose_refusal
from nexovue_platform.fixes.records import REQUESTER_NOTE_MAX_CHARACTERS, fix_branch
from nexovue_platform.fixes.sentences import FLAG_LABELS, NOT_CHECKED

# GitHub Actions' two skip instructions (FR23): the subject's marker, and the trailer.
SKIP_CI_MARKER: Final = "[skip ci]"
SKIP_CHECKS_TRAILER: Final = "skip-checks: true"
WHOLE_REPOSITORY_WORDS: Final = "the whole repository"

TITLE: Final = "Suggested change for {folder} (nexovue suggestion {fix_id})"
WHAT_THIS_IS: Final = (
    "Written by an AI model, on a person's request. Not compiled. Not run. Review it as you "
    "would any outside contribution."
)
CI_HELD: Final = (
    "Nexovue's commit asks GitHub Actions to hold `push` and `pull_request` runs. Other "
    "triggers, other CI systems and apps connected to this repository may still run. "
    "Required checks stay pending until CI runs. To run CI, use Run CI on the finding in "
    "Nexovue, after reading the change."
)
CI_ON_OPEN: Final = (
    "An owner chose to run CI when a draft opens for this repository, so this commit asks "
    "nothing to hold. CI runs the model's code, possibly with this repository's secrets."
)
FILES_HEADING: Final = "Files changed"
FLAGS_HEADING: Final = "Marked for a closer look"
NOT_CHECKED_HEADING: Final = "What Nexovue did not check"
NOTE_HEADING: Final = "The requester's note"
WHO_HEADING: Final = "Who asked"
WHY_HEADING: Final = "Why"
PRIVATE_REQUESTER: Final = "Requested by GitHub user {login}, through Nexovue."
PUBLIC_REQUESTER: Final = "Requested by a Nexovue {role}."
SIGNAL_LINE: Final = "The {signal} signal read {current} against a normal of {normal}."
CONSOLE_LINE: Final = "The finding in Nexovue: {url}"
# Why each file is in the change, by the role the pack job gave it (fixed words only).
FILE_ROLE_WORDS: Final[dict[str, str]] = {
    "stack": "named by the error's stack",
    "named": "named by the person who asked",
    "imported": "imported by a file the stack named",
    "asked": "asked for by the model and sent by the person who asked",
    "test": "the test file for a file the stack named",
}
UNKNOWN_ROLE_WORDS: Final = "read for this suggestion"


class NoteRefused(ValueError):  # noqa: N818 — named for what it reports
    """FR24a: the requester's note cannot go into a pull request, by a fixed code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    role: str
    added: int
    removed: int


@dataclass(frozen=True, slots=True)
class MarkedLine:
    kind: str
    path: str
    line: int | None


@dataclass(frozen=True, slots=True)
class SignalFacts:
    signal: str
    current: str
    normal: str


@dataclass(frozen=True, slots=True)
class PullRequestFacts:
    """Everything the text is built from, each Nexovue's own value."""

    fix_id: str
    folder: str | None
    private: bool
    ci_on_open: bool
    files: Sequence[ChangedFile]
    flags: Sequence[MarkedLine] = ()
    note: str | None = None
    # A public repository's note goes in only when the requester confirmed it (FR24a).
    note_confirmed: bool = False
    requester_login: str | None = None
    requester_role: str = "member"
    signal: SignalFacts | None = None
    console_url: str | None = None


@dataclass(frozen=True, slots=True)
class PullRequestText:
    title: str
    commit_message: str
    body: str
    branch: str


def checked_note(note: str | None) -> str | None:
    """FR24a's checks, at the click and again before the body is built: one line, at most
    200 characters, plain text under FR16's prose checks (no address, email or markup)."""
    if note is None or not note.strip():
        return None
    stripped = note.strip()
    if len(stripped) > REQUESTER_NOTE_MAX_CHARACTERS:
        raise NoteRefused("too_long")
    if "\n" in stripped or "\r" in stripped:
        raise NoteRefused("one_line")
    refusal = prose_refusal(stripped, None)
    if refusal is not None:
        raise NoteRefused(refusal)
    return stripped


def pull_request_text(facts: PullRequestFacts) -> PullRequestText:
    folder = facts.folder or WHOLE_REPOSITORY_WORDS
    title = TITLE.format(folder=folder, fix_id=facts.fix_id)
    subject = title if facts.ci_on_open else f"{title} {SKIP_CI_MARKER}"
    commit_message = subject if facts.ci_on_open else f"{subject}\n\n{SKIP_CHECKS_TRAILER}"
    return PullRequestText(
        title=title,
        commit_message=commit_message,
        body=_body(facts),
        branch=fix_branch(facts.fix_id),
    )


def _body(facts: PullRequestFacts) -> str:
    parts = [WHAT_THIS_IS, CI_ON_OPEN if facts.ci_on_open else CI_HELD, f"## {FILES_HEADING}"]
    parts.append(
        "\n".join(
            f"- {_code(each.path)}: {FILE_ROLE_WORDS.get(each.role, UNKNOWN_ROLE_WORDS)}, "
            f"+{each.added} -{each.removed}"
            for each in facts.files
        )
    )
    if facts.flags:
        parts.append(f"## {FLAGS_HEADING}")
        parts.append(
            "\n".join(
                f"- {_code(flag.path)}"
                + ("" if flag.line is None else f", line {flag.line}")
                + f": {FLAG_LABELS.get(flag.kind, flag.kind)}"
                for flag in facts.flags
            )
        )
    parts += [f"## {NOT_CHECKED_HEADING}", NOT_CHECKED]
    note = checked_note(facts.note)
    if note is not None and (facts.private or facts.note_confirmed):
        parts += [f"## {NOTE_HEADING}", _code(note)]
    parts += [f"## {WHO_HEADING}", _requester(facts)]
    why = _why(facts) if facts.private else []
    if why:
        parts += [f"## {WHY_HEADING}", "\n".join(why)]
    return "\n\n".join(parts) + "\n"


def _why(facts: PullRequestFacts) -> list[str]:
    """A private repository's signal, numbers and console link (design, U5)."""
    lines = []
    if facts.signal is not None:
        lines.append(
            SIGNAL_LINE.format(
                signal=facts.signal.signal,
                current=facts.signal.current,
                normal=facts.signal.normal,
            )
        )
    if facts.console_url:
        lines.append(CONSOLE_LINE.format(url=facts.console_url))
    return lines


def _requester(facts: PullRequestFacts) -> str:
    """A private repository names the login as plain text, never `@login`; a public one
    only the Nexovue role (A10)."""
    login = (facts.requester_login or "").lstrip("@")
    if facts.private and login:
        return PRIVATE_REQUESTER.format(login=login)
    return PUBLIC_REQUESTER.format(role=facts.requester_role)


def _code(value: str) -> str:
    """A code span no backtick inside can close, so a path or note is shown as written and
    no markup in it is followed (CommonMark's code spans). *Unverified, from memory:* that
    GitHub notifies no `@name` inside a code span; the founder's real run checks it
    (status, open decision 92)."""
    runs = [len(run) for run in re.findall(r"`+", value)]
    fence = "`" * (max(runs, default=0) + 1)
    padded = f" {value} " if value.startswith("`") or value.endswith("`") else value
    return f"{fence}{padded}{fence}"
