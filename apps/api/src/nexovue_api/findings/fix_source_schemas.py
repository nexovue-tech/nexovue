"""Rung 3's shapes on a finding: Ask, the file list, Send and Cancel (spec FR11 to FR15,
FR13a; design, *State by role*; plan T3.9, T3.11, T3.16).

A person's routes only: tokens are refused below each, so nothing here is on the read
surface (AC3). A viewer and a support view read a state and its sentence, never a path
(AC10, AC11).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, Literal

from nexovue_platform.fixes.records import HINT_MAX_CHARACTERS, MAX_FILES, MAX_TYPED_PATHS
from pydantic import BaseModel, Field

from nexovue_api.findings.fix_schemas import FixSectionLine, VerdictView
from nexovue_api.findings.schemas import ExplanationSegment

PATH_MAX_CHARACTERS: Final = 200
DIGEST_CHARACTERS: Final = 64

SourceState = Literal[
    "gathering",
    "gathered",
    "proposing",
    "proposed",
    "refused",
    "failed",
    "stale",
    "approved",
    "opened",
]
SourceResult = Literal["ready", "ready_without_stack"]


class SourceFixRequest(BaseModel):
    """Ask (FR11): from the error's stack, or from up to 5 typed paths (D4)."""

    paths: list[str] = Field(default_factory=list, max_length=MAX_TYPED_PATHS)
    # D46: email me when this needs me.
    notify: bool = True


class SourceFilesRequest(BaseModel):
    """FR13a: remove listed files, or type further paths; back to `gathering`."""

    remove: list[str] = Field(default_factory=list, max_length=MAX_FILES)
    add: list[str] = Field(default_factory=list, max_length=MAX_TYPED_PATHS)


class SourceSendRequest(BaseModel):
    """FR13a: Send is bound to the list on the person's screen by its digest."""

    files_digest: str = Field(min_length=DIGEST_CHARACTERS, max_length=DIGEST_CHARACTERS)
    confirm_top_left_out: bool = False
    hint: str | None = Field(default=None, max_length=HINT_MAX_CHARACTERS)


class ListedFile(BaseModel):
    ref: str
    path: str
    role: str
    # Nexovue's own sentence: "named at line 42 of the error's stack", "imported by …".
    why: str
    bytes: int
    lines_sent: list[int] | None
    moved_on_default: bool
    # GitHub's own page at the commit read, so GitHub decides who may open it (FR13a).
    github_url: str | None


class LeftOutFile(BaseModel):
    path: str
    why: str
    bytes: int | None


class TypedMiss(BaseModel):
    path: str
    sentence: str


class SourceFiles(BaseModel):
    """The list a requester confirms (FR13a). Paths that exist in the tree only (AC12g)."""

    asked_round: AskedRound | None = None
    files: list[ListedFile]
    left_out: list[LeftOutFile]
    unmatched: int
    typed_missing: list[TypedMiss]
    commit: str
    commit_known: bool
    commit_sentence: str | None
    top_left_out: bool
    digest: str
    bytes_sent: int
    provider_host: str
    round: int
    lapses_at: datetime
    forgeable: str
    send_host: str
    send: str


class SourceOffer(BaseModel):
    """Ask, with what it starts from (D35), tries left, and the link step (D20)."""

    action: str
    result: SourceResult
    label: str
    name_files_lead: str | None
    folder: str | None
    tries_left: int
    needs_link: bool
    link_sentence: str | None


class AskedFile(BaseModel):
    """FR15b, at round 2 or 3: a file the model asked for, why, and its words, labelled."""

    path: str
    because: str
    model_words: str | None


class AskedRefusal(BaseModel):
    path: str
    why: str


class AskedRound(BaseModel):
    """ "The AI asks for N more files" (design, *State by role*, `gathered` after asking)."""

    lead: str
    round_sentence: str
    model_words_label: str
    asked: list[AskedFile]
    refused: list[AskedRefusal]
    unmatched: int


class DiffLine(BaseModel):
    """One line of a hunk: its marker, its text with no line ending, and its numbers."""

    marker: Literal[" ", "-", "+"]
    text: str
    old: int | None
    new: int | None
    # FR18's labels beside this line ("new call"), Nexovue's own words.
    flags: list[str]


class DiffHunk(BaseModel):
    header: str
    lines: list[DiffLine]


class ProposalFlag(BaseModel):
    kind: str
    label: str
    path: str
    line: int | None
    side: Literal["new", "old", "file"]


class ProposalExplanation(BaseModel):
    """The model's `why`, checked again, its stand-ins restored as segments (AC56)."""

    segments: list[ExplanationSegment]


class ProposalFile(BaseModel):
    ref: str
    path: str
    github_url: str | None
    added: int
    removed: int
    # None when the context was too long to store: Prepare patch shows it (D53).
    hunks: list[DiffHunk] | None
    context_note: str | None
    open: bool
    explanations: list[ProposalExplanation]
    # Step 5: after a passed patch check the hunks are the check's, from the head; a file
    # that changed there since the suggestion says so (FR21's "moved").
    moved_note: str | None = None


class Proposal(BaseModel):
    """A written suggestion in the requester's shape (design, *Section parts*)."""

    lead: str
    starts_from: str | None
    facts_note: str
    flags: list[ProposalFlag]
    files: list[ProposalFile]
    explanation_label: str
    not_checked: list[str]
    files_read: list[ListedFile]
    left_out: list[LeftOutFile]
    written_at: datetime | None
    commit: str
    tokens: int
    # Step 5: set when the hunks above were rebuilt by this person's patch check.
    rebuilt_note: str | None = None


class CheckView(BaseModel):
    """This person's own check run (design, *The check run*): `running`, passed, or why not."""

    id: uuid.UUID
    purpose: str
    status: str
    heading: str
    sentence: str | None
    passed: bool
    expires_at: datetime | None


class PatchView(BaseModel):
    """This person's own patch check (design, *State by role*, check rows; FR20a): working,
    ready with its download, or why not. Never another person's (AC40)."""

    status: str
    heading: str
    sentence: str | None
    # Only while the check stands: this person's download, served from their own row.
    downloadable: bool
    expires_at: datetime | None


class DraftConfirm(BaseModel):
    """FR24's confirmation, from this person's own passed `draft` check: the facts in real
    values, the CI mode, every warning by exact string, and the digest the click carries."""

    repository: str
    branch: str
    file_count: int
    line_count: int
    facts: str
    ci_mode: str
    warnings: list[str]
    digest: str
    public: bool
    note_label: str
    note_public_confirm: str | None
    action: str
    expires_at: datetime


class DraftView(BaseModel):
    """Step 7, in this person's shape (design, *State by role*; FR10c, FR24, FR26).

    `action` is **Open draft pull request**, or None with `refusal` in its place (a hidden
    button, never a disabled one). `check` is this person's own `draft` check; `confirm`
    shows once it passed. `pull_request` and `failure` describe what was written, or why
    not. While write access is off in code, `refusal` says so and nothing else is offered.
    """

    action: str | None = None
    refusal: str | None = None
    needs_link: bool = False
    # D6: the account link step's sentence, when this person has none yet.
    link_sentence: str | None = None
    check: PatchView | None = None
    confirm: DraftConfirm | None = None
    failure: str | None = None
    pull_request_state: str | None = None
    pull_request_url: str | None = None
    pull_request_sentence: str | None = None
    ci_note: str | None = None
    delete_branch_action: str | None = None


class SourceFix(BaseModel):
    id: uuid.UUID
    state: SourceState
    heading: str
    sentence: str | None
    reason: str | None
    # Cancelled and lapsed read as a choice, with no fix id and no failure tone (AC46).
    neutral: bool
    files: SourceFiles | None
    requested_at: datetime
    state_changed_at: datetime
    # Step 4: the written suggestion, for a requester who may read its code.
    proposal: Proposal | None = None
    # The owner limits quoted code (D20) and this person has no passed check yet.
    code_withheld: str | None = None
    check_action: str | None = None
    check: CheckView | None = None
    # A viewer's counts (design, *State by role*, `proposed`).
    file_count: int | None = None
    line_count: int | None = None
    # Step 5: "Prepare patch", and this person's own patch check; FR29's question.
    patch_action: str | None = None
    patch: PatchView | None = None
    verdict: VerdictView | None = None
    # Step 7: the draft pull request, for a requester; a viewer reads the state's sentence.
    draft: DraftView | None = None


class DraftRequest(BaseModel):
    """`POST .../draft-pull-request` (FR22, FR24a): the digest of this person's own passed
    `draft` check, and their one optional line, confirmed for a public repository."""

    digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    note: str | None = Field(default=None, max_length=400)
    note_confirmed: bool = False


class DraftStarted(BaseModel):
    """The click's answer: the suggestion is `approved`, and its job is queued."""

    fix_id: uuid.UUID
    state: SourceState


class DeleteBranchStarted(BaseModel):
    fix_id: uuid.UUID


class EarlierSource(BaseModel):
    id: uuid.UUID
    state: SourceState
    requested_at: datetime


class FindingSourceFix(BaseModel):
    """`GET /findings/{id}/fixes/source`: rung 3 in this person's shape."""

    finding_id: uuid.UUID
    line: FixSectionLine | None
    offer: SourceOffer | None
    latest: SourceFix | None
    earlier: list[EarlierSource]
    support_view_note: str | None


class BriefFilesStarted(BaseModel):
    request_id: uuid.UUID


class BriefPath(BaseModel):
    path: str
    line: int | None


class BriefFiles(BaseModel):
    """FR5c: `pending` until the job answers, then the paths, which exist in the tree."""

    pending: bool
    paths: list[BriefPath]


class CheckRequest(BaseModel):
    """`POST .../check`: `read` is **Check my access** (Step 4), `patch` is **Prepare
    patch** (Step 5), `draft` opens the draft's confirm (Step 7)."""

    for_: Literal["read", "patch", "draft"] = Field(alias="for")
