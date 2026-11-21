"""F5's shapes on a finding (spec FR1 to FR5; design, *Routes*): `GET /findings/{id}/fixes`.

A person's read only: tokens are refused below the route, so nothing here is on the
read surface (AC3). Plan Step 1 carries rungs 1 and 2; later steps add the latest fix
and older fixes beside them.
"""

from __future__ import annotations

import uuid
from typing import Literal

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


class FixSectionLine(BaseModel):
    """The *Suggested fix* section's line for this person (design, *State by role*).
    Absent when a role has no line, so neither the section nor its heading renders."""

    heading: str | None
    sentence: str


class FindingFixes(BaseModel):
    """`GET /findings/{id}/fixes`: rungs 1 and 2 and, from later steps, the section."""

    finding_id: uuid.UUID
    # None when F28's own sentence says all there is: no release, or one over a day old.
    deploy_line: DeployLineAddition | None
    # None for a finding closed as a false alarm or because its service went (FR5).
    brief: FixBrief | None
    # None on the hosted service, and while no role has a line (plan Step 1).
    section: FixSectionLine | None
