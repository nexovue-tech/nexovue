"""F5's shapes on a finding (spec FR1 to FR5, FR31 to FR38; design, *Routes*).

`GET /findings/{id}/fixes` carries rungs 1 and 2; `GET` and `POST
/findings/{id}/fixes/general` carry rung 2b, on their own path so the first never reaches
AI code (AC33). A person's reads only: tokens are refused below each route, so nothing here
is on the read surface (AC3, AC74).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from nexovue_platform.fixes.records import FixVerdict, GeneralLanguage
from pydantic import BaseModel

from nexovue_api.findings.schemas import ExplanationSegment

# CI's last word on a release (`releases.last_state`).
LastState = Literal["started", "finished", "failed", "rolled_back"]


class EarlierRelease(BaseModel):
    """The version that ran on the service before F28's *release before this* (FR1)."""

    release_id: uuid.UUID
    version: str
    # CI's last word on it: a failed or rolled-back version is never offered unsaid.
    last_state: LastState | None


class DeployLineAddition(BaseModel):
    """What F5 adds to F28's *Release before this* row (FR1, AC34). Never a line of its own:
    the page draws these sentences after F28's, and only beside the release named here."""

    # F28's release before the finding, which these sentences extend.
    release_id: uuid.UUID
    earlier: EarlierRelease | None
    # Each a fixed template (`fix_facts.py`), as segments: names are telemetry text.
    sentences: list[list[ExplanationSegment]]


class FixBrief(BaseModel):
    """Rung 2 (FR2 to FR5): the text a person copies into their own coding tool."""

    text: str


class FindingFixes(BaseModel):
    """`GET /findings/{id}/fixes`: rungs 1 and 2, with AI off or on."""

    finding_id: uuid.UUID
    # None when F28's own sentence says all there is: no release, or one over a day old.
    deploy_line: DeployLineAddition | None
    # None for a finding closed as a false alarm or because its service went (FR5).
    brief: FixBrief | None


# ------------------------------------------------------------- rung 2b

GeneralState = Literal["proposing", "proposed", "refused", "failed"]


class FixSectionLine(BaseModel):
    """The *Suggested fix* section's line for this person (design, *State by role*): why the
    action is not offered, or the line the offer stands under. Absent when this role has
    none, so neither the section nor its heading renders for that alone (FR10)."""

    heading: str
    sentence: str


class GeneralOffer(BaseModel):
    """FR31 to FR33: **Suggest a general fix** is offered to this person now."""

    action: str
    lead: str
    # FR33's send sentence, naming the provider's host.
    send_sentence: str
    languages: list[GeneralLanguage]
    # From the service's reported SDK language once it is read (FR33); none yet.
    preselected: GeneralLanguage | None
    tries_left: int


class FactReference(BaseModel):
    """A fact the suggestion rests on: F3's evidence id, and its number on the page."""

    evidence_id: str
    number: int


class GeneralContent(BaseModel):
    """FR37, in its order: the label first, Nexovue's lead sentence, then the model's
    checked words with aliases restored as segments, and the example as one text value."""

    heading: str
    label: str
    lead: str
    summary: list[ExplanationSegment]
    steps: list[list[ExplanationSegment]]
    language: GeneralLanguage
    code: str
    evidence: list[FactReference]
    # "Written from an earlier reading of this finding": F3 has a newer version.
    earlier_reading: str | None


class VerdictAnswer(BaseModel):
    value: FixVerdict
    label: str


class VerdictView(BaseModel):
    """FR29's question, its three answers, and the one recorded, if any (UR11)."""

    question: str
    answers: list[VerdictAnswer]
    chosen: FixVerdict | None
    # "Recorded. This changes nothing else." once an answer is stored.
    recorded: str | None


class VerdictRequest(BaseModel):
    verdict: FixVerdict


class GeneralFix(BaseModel):
    """One general suggestion, in this person's shape. `content` only at `proposed`, and
    only when G1 to G9 pass again on this read (FR36, AC71)."""

    id: uuid.UUID
    state: GeneralState
    heading: str
    sentence: str | None
    content: GeneralContent | None
    tokens_used: int
    requested_at: datetime
    finished_at: datetime | None
    # Step 5: "Was this suggestion useful?", for a person who may ask, at `proposed`.
    verdict: VerdictView | None = None


class EarlierGeneral(BaseModel):
    """An older suggestion, as a quiet list (FR10a): state and time."""

    id: uuid.UUID
    state: GeneralState
    requested_at: datetime


class FindingGeneralFix(BaseModel):
    """`GET /findings/{id}/fixes/general`: rung 2b for this person. Every part empty means
    nothing about it renders (FR31, AC67)."""

    finding_id: uuid.UUID
    line: FixSectionLine | None
    offer: GeneralOffer | None
    latest: GeneralFix | None
    earlier: list[EarlierGeneral]


class GeneralFixRequest(BaseModel):
    """FR33: the picked language, from the fixed list. Nothing typed."""

    language: GeneralLanguage
