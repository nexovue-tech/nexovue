"""Finding shapes (F6, ADR-022): the feed, one finding and its page, the false-finding
count, and what a person may do to a finding (FR19 to FR21).

The vocabularies are `nexovue_platform.detection`'s, spelled as literals so the
OpenAPI document names them; `test_findings.py` holds the two equal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from nexovue_platform.connections import MAX_PASTED_ADDRESS_LENGTH
from pydantic import BaseModel, ConfigDict, Field, model_validator

FindingScope = Literal["service", "project"]
FindingSeverity = Literal["critical", "warning"]
# Acknowledged is derived, not stored: an open finding somebody has acknowledged (FR19).
FindingState = Literal["open", "acknowledged", "closed"]
ClosedReason = Literal[
    "recovered", "false_alarm", "new_normal", "adopted", "service_gone", "could_not_be_judged"
]
SignalName = Literal["slower", "failing", "traffic"]
SignalUnit = Literal["ms", "%", "rpm"]
# What a feed request asks for. `open` includes acknowledged findings.
FeedState = Literal["open", "closed", "all"]

# ---- the case (F22): whether people are working, apart from the signal.
CaseState = Literal["investigating", "resolved"]
CaseResolution = Literal[
    "resolved", "false_alarm", "new_normal", "tracker_closed", "no_activity", "recovered_untouched"
]
# Whether the problem is measured now for the whole case: open if the root or
# any joined finding is open (spec, *Words used here*).
CaseSignal = Literal["open", "closed"]
# Whether the assignment email will be tried (FR5); the screen says the true thing.
EmailOutcome = Literal["will_try", "not_configured", "rate_limited", "none"]
# The feed's case filters (FR22): roots only, muted included, across a person's
# organization. `mine` is a person's alone.
CaseFilter = Literal["active", "mine", "resolved"]
# How an active case will end by itself, when the page and row should say so:
# 90 days without a person's act (FR26), or 72 hours after recovery untouched (FR49).
CaseEndsBy = Literal["idle", "untouched"]
FindingEventKind = Literal[
    "finding_opened",
    "severity_raised",
    "signal_joined",
    "finding_closed",
    "flapping_started",
    "finding_joined_case",
    "finding_left_case",
    "case_started",
    "acknowledged",
    "assigned",
    "unassigned",
    "case_resolved",
    "case_reopened",
    "false_alarm",
    "new_normal",
    "note_added",
    "note_deleted",
    "link_added",
    "link_removed",
    "link_promoted",
    "issue_created",
    "issue_closed",
    "issue_reopened",
    "issue_gone",
    "issue_moved",
    "tracker_write_failed",
    "explained",
]
EventActorKind = Literal["person", "system", "tracker"]


class StoredSignal(BaseModel):
    """One entry of `findings.signals` as the project job writes it, leading signal first.

    Its unit is not stored: it follows from the signal.
    """

    signal: SignalName
    # The job's key for the entry: the signal, or "endpoint:<name>" for an endpoint failing
    # on its own. None for a finding stored before keys were kept.
    key: str | None = None
    # None when there was no reading, never 0: a missing number and a real zero differ.
    normal: float | None
    current: float | None
    worst: float | None
    # The sentence naming what it was compared with ("Tuesdays around 15:00, last 3 weeks").
    comparison: str
    endpoint: str | None = None
    # The normal's band when the finding was judged (FR16), so a closed finding whose
    # readings expired still shows it. None for a finding stored before it was kept.
    band_low: float | None = None
    band_high: float | None = None
    # The evidence endpoint's value then and in the same window last week (FR3): its p95
    # for *slower*, its share failing for *failing*. None when no week compared.
    endpoint_current: float | None = None
    endpoint_last_week: float | None = None


class FindingSignal(StoredSignal):
    unit: SignalUnit


class Finding(BaseModel):
    """What any reader sees, a person or an API token."""

    id: uuid.UUID
    project_id: uuid.UUID
    scope: FindingScope
    # None for a project finding, which spans several services.
    service_name: str | None
    title: str
    severity: FindingSeverity
    state: FindingState
    closed_reason: ClosedReason | None
    muted: bool
    # The service's snooze and mute as they stand now, not when the finding opened.
    service_snoozed_until: datetime | None
    service_muted_until: datetime | None
    started_at: datetime
    closed_at: datetime | None
    acknowledged_at: datetime | None
    signals: list[FindingSignal]
    # Up to the last 60 readings of the leading signal, oldest first; empty when none exist.
    trend: list[float]
    # ---- the case (F22). Null throughout means nobody ever took it.
    case_state: CaseState | None
    case_resolution: CaseResolution | None
    case_opened_at: datetime | None
    case_resolved_at: datetime | None
    # The case this finding joined (FR9, FR12); its page leads to the root.
    root_finding_id: uuid.UUID | None
    # Open if this finding or any open joined finding is open: the whole case's signal.
    case_signal: CaseSignal
    # The worse of this finding's severity and an open joined finding's (FR21).
    case_severity: FindingSeverity
    # The open joined finding, when one is open on a root (FR23's "came back").
    open_joined_finding_id: uuid.UUID | None
    open_joined_since: datetime | None


class SignalPoint(BaseModel):
    """One judged minute of one signal, in the signal's unit."""

    at: datetime
    # None when the minute had nothing to measure: no p95, no requests, no normal.
    value: float | None
    normal: float | None
    # Where the sensitivity's minimum change lies (spec, *Product values*): above the
    # normal for *slower* and *failing more*, below it for *losing traffic*. Between
    # it and the normal is the band a reading may move in without being unusual.
    edge: float | None


class SignalSeries(BaseModel):
    """One signal's readings against its normal band, oldest first (AC38)."""

    signal: SignalName
    unit: SignalUnit
    points: list[SignalPoint]


class LinkedBurn(BaseModel):
    """A reliability target's burn beside the finding (F19 FR20, AC85): nexovue's label
    for the target, never its name, and the figures the job keeps current on the
    incident while it is open. A figure the burn never had is None, never 0."""

    slo_id: uuid.UUID | None
    # Composed by nexovue from the service, endpoint or check names (D43): telemetry
    # text, so a reader that is not a person labels it untrusted.
    label: str
    kind: str | None
    rate: float | None
    hours_left: float | None
    allowed: int | None
    left: int | None


class LinkedIncident(BaseModel):
    """A threshold or *went silent* incident on the same service (BR2, FR16), or a
    reliability target's burn on it (F19 FR20)."""

    id: uuid.UUID
    rule_name: str
    condition_kind: str
    opened_at: datetime
    resolved_at: datetime | None
    burn: LinkedBurn | None = None


class ReleaseFact(BaseModel):
    """One release period as the finding page states it (F28 FR32, FR52): order, never
    cause. No sender text."""

    release_id: uuid.UUID
    period_id: uuid.UUID
    service_name: str
    version: str
    started_at: datetime
    start_basis: Literal["seen", "reported"]
    # Told by CI, counted in traffic, or both: "Reported by your CI, seen in traffic."
    basis: Literal["reported", "seen", "both"]
    last_state: Literal["started", "finished", "failed", "rolled_back"] | None
    is_return: bool
    already_running: bool
    # Whether the finding's service calls this one, from `service_edges_1m`; None when
    # unknown (a project finding, the same service, or a failed read).
    called: bool | None = None


class ReleaseContext(BaseModel):
    """What came before a finding (F28 FR32, FR33, FR52; design, *Finding page*)."""

    # A service finding's last period starting at or before it, at any age.
    before: ReleaseFact | None
    # The service's coverage as of the finding's start; None for a project finding.
    coverage: Literal["deploys_and_versions", "deploys_only", "versions_only", "none"] | None
    last_reported_at: datetime | None
    first_heard_at: datetime | None
    # Every release in the project in the 2 hours before, this service's first.
    nearby: list[ReleaseFact]
    # Whether `called` could be read; when not, the list stands unmarked.
    called_known: bool
    # A project finding: each affected service's latest release in the 24 hours
    # before, newest first. Empty for a service finding.
    affected: list[ReleaseFact]


class FindingDetail(Finding):
    """One finding as its page reads it (FR31)."""

    # Each signal's readings while they last (35 days at most); empty once expired,
    # and always for a project finding, which has no readings of its own (AC40).
    series: list[SignalSeries]
    incidents: list[LinkedIncident]
    # A project finding's services (FR15); empty for a service finding.
    services: list[str]
    # F28: the release before it, its service's coverage and releases nearby.
    release_context: ReleaseContext
    # F3: until when the raw traces and logs of its window are kept (design, *Links*);
    # None when the project's retention cannot be read.
    raw_kept_until: datetime | None = None


class ProposedShift(BaseModel):
    """What *This is the new normal* would do to one signal (FR21), in its unit."""

    signal: SignalName
    unit: SignalUnit
    ratio: float
    normal_before: float | None
    normal_after: float | None


class NewNormalPreview(BaseModel):
    """Whether an open finding's level is known well enough to become its normal, and
    what that normal would be: the dangerous action states its effect in real numbers
    before it can be confirmed (design, *The finding page*)."""

    available: bool
    # The fewest readings any of its signals has, and how many it needs.
    readings: int
    needed: int
    signals: list[ProposedShift]


class EarlierCase(BaseModel):
    """The service's latest other case (FR9, FR13), for a finding that holds none:
    a quiet active case that taking this joins, or a resolved one it can name,
    and reopen when it ended without a person's decision (AC95)."""

    id: uuid.UUID
    case_state: CaseState
    case_resolution: CaseResolution | None
    case_resolved_at: datetime | None
    # The last person's act on it: "nobody has touched since 12 Oct".
    case_activity_at: datetime


LinkStatus = Literal[
    "creating",
    "promoting",
    "in_sync",
    "waiting",
    "needs_attention",
    "refused",
    "gone",
    "not_followed",
    "stopped",
]
LinkRole = Literal["synced", "reference"]
IssueState = Literal["open", "closed"]


class CaseLink(BaseModel):
    """One tracker issue on a case (F22, FR35 to FR41), as a person reads it. The address
    is built by nexovue from the strict key, never stored (D81); no title or tracker
    text exists to show. A token never sees links (FR20)."""

    id: uuid.UUID
    kind: str | None
    role: LinkRole
    sync_status: LinkStatus
    # `acme/api#412`; None while it is being created, or if creation was refused.
    external_key: str | None
    url: str | None
    # The repository it lives in, by its strict key; None once that place is gone.
    place_key: str | None
    # Anyone can read that repository now (FR42): issues there are already exposed, and
    # the page says so. Whether an admin allowed it decides only creating and promoting.
    place_public: bool = False
    seen_state: IssueState | None
    wanted_state: IssueState | None
    # A comment is owed and not yet written (BR10).
    comment_owed: bool
    # Closed in the tracker while the case signal was open: the hold (D73).
    tracker_closed_at: datetime | None
    last_synced_at: datetime | None
    created_at: datetime
    # Why it waits or needs a person, as an enum (FR44); the console words it.
    last_failure: str | None
    # Whether a retry can help (FR44): offered only then.
    retry_helps: bool


class FindingForMember(FindingDetail):
    """A signed-in person's view of one finding. An API token never sees these fields:
    they name a person and quote what they wrote (design 2.1, item 8), or prepare an
    action only a person can take."""

    acknowledged_by: uuid.UUID | None
    acknowledged_by_name: str | None
    feedback_reason: str | None
    # None when the finding is closed, or is a project's, which has no normal to move.
    new_normal: NewNormalPreview | None
    # ---- the case, the person's half (F22 FR20): a token never sees these.
    assignee_id: uuid.UUID | None
    assignee_name: str | None
    # When the current assignee took or was given the case: "Asha took this at
    # 14:06" is a fact with its time (D76).
    case_taken_at: datetime | None
    # On a root with an open joined finding: whether the assignee has acted since
    # it came back (FR12, AC100). None when nothing joined is open.
    assignee_acted_since_join: bool | None
    case_resolved_by_name: str | None
    # FR48: whether to say "PagerDuty is not told" after Take this and resolve.
    project_has_pagerduty: bool
    # Whether this install can send the assignment email at all (FR5).
    email_available: bool
    # ---- keeping and ending (F22 T2.7)
    # On a finding with no case of its own and none joined: the service's latest
    # other case, so the page can name it (FR9, FR13; AC15, AC68, AC95).
    earlier_case: EarlierCase | None
    # The case's last person's act; with `case_ends_on`, "No activity for 76 days".
    case_activity_at: datetime | None
    # When an active case ends by itself, set only when the page should say so:
    # from 14 days before an idle end, or from recovery for an untouched case.
    case_ends_on: datetime | None
    case_ends_by: CaseEndsBy | None
    # When a resolved case's closed finding will be deleted (FR27, AC112).
    deletes_on: datetime | None
    # How many findings that joined this case are still kept (FR24, AC112).
    joined_kept_count: int
    # ---- the case's tracker issues (F22 T4.3), the root's for a joined finding.
    links: list[CaseLink] = Field(default_factory=list)


# Long enough for a sentence or two about why; bounded so a reason is not a document.
FEEDBACK_REASON_MAX_LENGTH = 1000


class FalseAlarmRequest(BaseModel):
    reason: Annotated[str, Field(max_length=FEEDBACK_REASON_MAX_LENGTH)] | None = None


class FindingChanged(BaseModel):
    """What an action did, for the page's success state (design, *The finding page*):
    the finding as it now is, and the channels its *resolved* message goes to."""

    finding: FindingForMember
    resolved_to: list[str]


class CaseChangeRequest(BaseModel):
    """`PATCH /findings/{id}/case` (F22): start, take, assign, resolve, reopen.

    `assignee_id` present means an assignment, and an explicit null unassigns;
    absence means the field is not this call's business, which is why the service
    reads `model_fields_set` rather than the values alone. The `expected_*`
    fields carry what the page last showed, for FR4's 409.
    """

    case_state: CaseState | None = None
    assignee_id: uuid.UUID | None = None
    expected_assignee_id: uuid.UUID | None = None
    expected_case_state: CaseState | None = None
    # FR6, D72: resolve here and leave the synced issue open: no close, no comment.
    leave_issue_open: bool = False


class CaseChanged(BaseModel):
    """What a case action did: the finding as it now is, and whether an email will
    be tried (FR5), so the screen can say the true thing."""

    finding: FindingForMember
    email_outcome: EmailOutcome


# FR14: a note is plain text, not a document.
NOTE_BODY_MAX_LENGTH = 4000


class NoteRequest(BaseModel):
    body: Annotated[str, Field(min_length=1, max_length=NOTE_BODY_MAX_LENGTH)]


class CaseNote(BaseModel):
    id: uuid.UUID
    # Null once the author's user row is gone; the screen reads *Former member*.
    author_id: uuid.UUID | None
    author_name: str | None
    body: str
    created_at: datetime


class NoteWritten(BaseModel):
    """A note landed, on the root when written from a joined finding's page."""

    note: CaseNote
    finding: FindingForMember


class TimelineEvent(BaseModel):
    """One timeline row as an API token reads it: kinds and times only (FR20)."""

    id: uuid.UUID
    kind: FindingEventKind
    at: datetime


class TimelineEventForMember(TimelineEvent):
    """A person's row: who did it, the typed detail, and a note's words.

    `note_body` is joined from `finding_notes`, so a deleted note's text is gone
    (AC19); during a support view it reads the fixed hidden sentence (D62).
    """

    actor_kind: EventActorKind
    actor_id: uuid.UUID | None
    actor_name: str | None
    # Enums, ids and numbers only, by the writer's construction (AC24).
    detail: dict[str, Any]
    note_body: str | None


class TimelineResponse(BaseModel):
    events: list[TimelineEvent] | list[TimelineEventForMember]
    # Pass back as `?before=` to read the next 100 older rows; null at the end.
    next_before: str | None


class FindingFeedRow(Finding):
    """A person's feed row (F22 FR23, AC71): who has the case, read in the feed's
    one statement, and the case's end in its last days. An API token's rows are
    plain `Finding`s, naming nobody."""

    assignee_id: uuid.UUID | None
    assignee_name: str | None
    case_taken_at: datetime | None
    assignee_acted_since_join: bool | None
    # "Asha resolved the case at 16:10", in *Resolved cases*.
    case_resolved_by_name: str | None
    case_activity_at: datetime | None
    case_ends_on: datetime | None
    case_ends_by: CaseEndsBy | None
    # The row's project, for the case filters, which read every project.
    project_name: str
    # F3 (FR18): the card's cause line as segments; None with no cause or while held.
    likely_cause_sentence: list[ExplanationSegment] | None = None


class FindingListResponse(BaseModel):
    # A person's rows first: the union serializes each list by its own items' type.
    findings: list[FindingFeedRow] | list[Finding]
    # How many findings in the asked-for state the feed leaves out because they are muted.
    muted_count: int
    # F36, AC20: a capped list says when the cap bit.
    truncated: bool = False


class ServiceFindingStats(BaseModel):
    """FR26 for one service: its findings opened over the window, and its false alarms."""

    service_name: str
    opened: int
    false_alarms: int
    # The service's own measure against the budget of one false finding a week.
    false_alarms_per_week: float


class FindingStats(BaseModel):
    """FR26: findings opened over a window, and how many were false alarms, for the
    project and for each of its services."""

    days: int
    opened: int
    # Of the findings opened in the window, those marked a false alarm, whenever marked.
    false_alarms: int
    # Services with at least one judged minute: the denominator's services.
    judged_services: int
    # false_alarms / (judged_services * days / 7), the budget's own measure (spec,
    # *False-alarm budget*). None when no service has been judged.
    false_finding_rate_per_service_week: float | None
    # Each service with a finding opened in the window, most false alarms first. A
    # project finding spans several services, so it counts for the project alone.
    services: list[ServiceFindingStats]


class IssuePreview(BaseModel):
    """Exactly what an issue would say (FR35, AC83): fixed words, numbers and the
    service's strict name, built by the same function the job writes with."""

    place_id: uuid.UUID
    place_key: str
    title: str
    body: str
    # BR12: a public repository nobody allowed refuses creation; the panel says so.
    public: bool
    refusal: str | None


class CreateLinkRequest(BaseModel):
    """`POST /findings/{id}/links`: either `place_id`, to create an issue in one of the
    connection's places, or `url`, a pasted issue address to attach as a reference
    (FR40). Exactly one."""

    place_id: uuid.UUID | None = None
    url: str | None = Field(default=None, max_length=MAX_PASTED_ADDRESS_LENGTH)

    @model_validator(mode="after")
    def _one_of_the_two(self) -> CreateLinkRequest:
        if (self.place_id is None) == (self.url is None):
            raise ValueError("send either place_id, to create an issue, or url, to attach one")
        return self


class LinkChanged(BaseModel):
    """What a link act did: the finding as it now is, and the link it made or moved."""

    finding: FindingForMember
    link_id: uuid.UUID


# ------------------------------------------------------------- F3: the explanation


class ExplanationSegment(BaseModel):
    """One piece of a code-written sentence: plain text, a customer's name, or a time the
    console formats in the project's zone. Never markup, never a link (design, *Segments*)."""

    text: str | None = None
    name: str | None = None
    at: datetime | None = None


ExplanationKind = Literal[
    "deploy",
    "error_group",
    "dependency",
    "check",
    "incident",
    "other_finding",
    "traffic",
    "infrastructure",
    "absence",
]
ExplanationBasis = Literal["verified", "telemetry", "telemetry_text"]


class ExplanationFactTarget(BaseModel):
    """Where a fact's link goes, as the values it was read with (F3 FR16, AC105). The
    console builds the link (`features/findings/evidence-links.ts`); this only says
    what the data is. Names are customer text, shown and sent as text only."""

    kind: Literal["error_group", "dependency", "deploy", "host", "finding", "check", "incident"]
    # An error group's id and the services it was seen in.
    group_id: str | None = None
    services: list[str] = []
    # A dependency: who called whom, the attribute a told-only callee was named by, and
    # what moved (failure shares and p95 in milliseconds, before and during).
    caller: str | None = None
    callee: str | None = None
    callee_attr: str | None = None
    failure_share_before: float | None = None
    failure_share_during: float | None = None
    p95_before: float | None = None
    p95_during: float | None = None
    # A deploy's release, when F28 filed one; a finding's, check's or incident's id.
    release_id: str | None = None
    id: str | None = None


class ExplanationFact(BaseModel):
    """One frozen fact, numbered as the page shows it (FR4, FR44)."""

    id: str
    number: int
    kind: ExplanationKind
    basis: ExplanationBasis | None
    sentence: list[ExplanationSegment]
    # When the fact happened, where it has a moment of its own.
    at: datetime | None
    # Set when this fact was carried from an earlier read because this one failed.
    read_at: datetime | None
    # `last_before` and `near`: F28's release facts, shown in the section's first row.
    role: Literal["last_before", "near"] | None
    # An absence's reason, and the read a `could_not_read` names (FR6, FR45).
    absence: str | None
    read: str | None
    # Whether this fact is the cause's or supports it (FR44's order is already applied).
    is_cause: bool
    supports_cause: bool
    # Where the fact's link goes, for a kind that has one (F3 Step 2, FR16).
    target: ExplanationFactTarget | None = None


class ExplanationCause(BaseModel):
    """The likely place to look first, by rule (FR8). Only when released (FR50)."""

    evidence_id: str
    rule: str
    strength: Literal["likely", "possible"]
    basis: ExplanationBasis
    same_minute: bool
    header: list[ExplanationSegment]
    card: list[ExplanationSegment]


ExplanationState = Literal["reading", "not_read", "read"]
RatingReason = Literal["wrong_cause", "not_useful", "bad_wording"]


class ExplanationRating(BaseModel):
    """A person's "Was this useful?" (FR20). Carried to later versions unless the likely
    cause changed (AC83)."""

    version: int
    helpful: bool
    reason: RatingReason | None


WordingState = Literal["off", "pending", "done", "failed", "over_limit", "abstained", "held"]


class FindingExplanation(BaseModel):
    """`GET /findings/{id}/explanation` (design, *API*). Postgres only (A32)."""

    finding_id: uuid.UUID
    # `reading`: no version yet and a read is queued; `not_read`: none, and none queued.
    state: ExplanationState
    version: int | None
    latest_version: int | None
    read_at: datetime | None
    reads_failed: int
    facts: list[ExplanationFact]
    # None with no cause, and while a stored cause is held (FR50, AC92).
    likely_cause: ExplanationCause | None
    cause_held: bool
    # The previous version's cause line, when the cause changed since (FR3).
    previous_cause: list[ExplanationSegment] | None
    # How many open findings in the project name the same cause (FR47), computed by code.
    shared_cause_count: int
    # Wording (plan Step 5): a token never reads the words (BR10, A68).
    wording_state: WordingState
    wording: None = None
    # When a person may next ask for the facts to be read again (FR41).
    next_read_at: datetime | None
    # FR20: the reasons a "No" may give for this version, and this person's own
    # rating, carried from an earlier version with the same cause. Neither for a token.
    rating_reasons: list[RatingReason] = []
    my_rating: ExplanationRating | None = None


class ExplanationFeedbackRequest(BaseModel):
    """`POST .../explanation/feedback` (FR20). "Yes", or "No" with a reason from a
    closed list; never free text (A21)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    helpful: bool
    reason: RatingReason | None = None


RuleName = Literal[
    "signed_deploy",
    "dependency_finding",
    "signed_deploy_weak",
    "dependency_deploy",
    "dependency_edge",
    "traffic_rose",
    "project_deploy",
    "project_shared_dependency",
]


class RuleCount(BaseModel):
    """One rule's numbers on `/settings/explanations` (FR54): counts only, no names."""

    rule: RuleName
    # Findings whose latest explanation names a cause by this rule.
    named: int
    useful: int
    wrong_cause: int
    not_useful: int
    bad_wording: int
    stopped: bool


class RuleCountsResponse(BaseModel):
    rules: list[RuleCount]
    # Ratings of explanations that named no cause.
    without_cause_useful: int
    without_cause_not_useful: int


class StoppedRule(BaseModel):
    rule: RuleName
    stopped: bool


class ReadAgainAccepted(BaseModel):
    """`POST .../explanation/read-again`: queued, and when the next may be asked."""

    queued: bool
    next_read_at: datetime
