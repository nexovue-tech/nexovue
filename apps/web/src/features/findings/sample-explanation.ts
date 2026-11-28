import type { ExplanationFact, FindingDetail, FindingExplanation, ReleaseContext } from "./api";

/**
 * F3 FR46: a labelled sample of what lines up with a finding, from fixed data, so a
 * person can see the page before a real finding exists. Three cases: a likely cause, an
 * honest "not enough evidence", and facts that could not be read. Nothing here is a
 * reading of anyone's system, and the page that shows it makes no API call.
 *
 * Times are relative to the moment the page opens, so the sample never reads as stale.
 */

export type SampleCase = "likely" | "abstained" | "absences";

export const SAMPLE_CASES: readonly { id: SampleCase; label: string }[] = [
  { id: "likely", label: "A likely place to look" },
  { id: "abstained", label: "Not enough evidence" },
  { id: "absences", label: "Facts that could not be read" },
];

/** FR55: what a first-week install will really show, said beside the sample. */
export const FIRST_WEEK =
  "In its first week an install shows facts, and causes only from rules that need no " +
  "history: deploy causes reach “likely” once deploy notifications have run for 7 days.";

const MINUTE = 60_000;
const SAMPLE_ID = "sample";

function at(now: number, minutesAgo: number): string {
  return new Date(now - minutesAgo * MINUTE).toISOString();
}

const NO_RELEASES: ReleaseContext = {
  before: null,
  coverage: "deploys_only",
  last_reported_at: null,
  first_heard_at: null,
  nearby: [],
  called_known: true,
  affected: [],
};

export function sampleFinding(now = Date.now()): FindingDetail {
  return {
    id: SAMPLE_ID,
    project_id: SAMPLE_ID,
    scope: "service",
    service_name: "checkout",
    title: "checkout is 3× slower than normal",
    severity: "warning",
    state: "open",
    closed_reason: null,
    muted: false,
    service_snoozed_until: null,
    service_muted_until: null,
    started_at: at(now, 12),
    closed_at: null,
    acknowledged_at: null,
    signals: [],
    trend: [],
    case_state: null,
    case_resolution: null,
    case_opened_at: null,
    case_resolved_at: null,
    root_finding_id: null,
    case_signal: "open",
    case_severity: "warning",
    open_joined_finding_id: null,
    open_joined_since: null,
    series: [],
    incidents: [],
    services: [],
    release_context: NO_RELEASES,
    raw_kept_until: null,
  };
}

function fact(
  number: number,
  overrides: Partial<ExplanationFact> & Pick<ExplanationFact, "kind" | "sentence">,
): ExplanationFact {
  return {
    id: `sample:${number}`,
    number,
    basis: null,
    at: null,
    read_at: null,
    role: null,
    absence: null,
    read: null,
    is_cause: false,
    supports_cause: false,
    target: null,
    ...overrides,
  };
}

function base(now: number): FindingExplanation {
  return {
    finding_id: SAMPLE_ID,
    state: "read",
    version: 1,
    latest_version: 1,
    read_at: at(now, 6),
    reads_failed: 0,
    facts: [],
    likely_cause: null,
    cause_held: false,
    previous_cause: null,
    shared_cause_count: 0,
    wording_state: "off",
    wording: null,
    next_read_at: null,
    rating_reasons: [],
    my_rating: null,
  };
}

export function sampleExplanation(which: SampleCase, now = Date.now()): FindingExplanation {
  const deployedAt = at(now, 16);
  if (which === "likely") {
    return {
      ...base(now),
      likely_cause: {
        evidence_id: "sample:1",
        rule: "signed_deploy",
        strength: "likely",
        basis: "verified",
        same_minute: false,
        header: [
          { text: "Likely: the deploy of " },
          { name: "checkout" },
          { text: " " },
          { name: "v2.1" },
          {
            text:
              ", 4 minutes before this (3 deploys in 7 days). The deploy is a signed record; " +
              "the link to this finding is timing only.",
          },
        ],
        card: [
          { text: "Likely: deploy of " },
          { name: "checkout" },
          { text: " " },
          { name: "v2.1" },
          { text: " (3 in 7 days)" },
        ],
      },
      facts: [
        fact(1, {
          kind: "deploy",
          basis: "verified",
          is_cause: true,
          at: deployedAt,
          sentence: [
            { text: "Deploy of " },
            { name: "checkout" },
            { text: " " },
            { name: "v2.1" },
            { text: " deployed at " },
            { at: deployedAt },
            { text: ", 4 minutes before this. 3 deploys in 7 days." },
          ],
        }),
        fact(2, {
          kind: "dependency",
          basis: "telemetry",
          supports_cause: true,
          sentence: [
            { text: "Calls to " },
            { name: "payments" },
            { text: ": failures 0.4% → 6%, 1,240 calls in this window." },
          ],
        }),
        fact(3, {
          kind: "error_group",
          basis: "telemetry_text",
          supports_cause: true,
          sentence: [
            { text: "New error: " },
            { name: "TimeoutError" },
            { text: ", 214 times. Not seen in the hour before." },
          ],
        }),
      ],
    };
  }
  if (which === "abstained") {
    return {
      ...base(now),
      facts: [
        fact(1, {
          kind: "traffic",
          basis: "telemetry",
          sentence: [
            { text: "Traffic to " },
            { name: "checkout" },
            { text: " was steady before it slowed." },
          ],
        }),
        fact(2, {
          kind: "absence",
          absence: "no_deploy_information",
          sentence: [{ text: "No deploy information for this service." }],
        }),
      ],
    };
  }
  return {
    ...base(now),
    reads_failed: 1,
    facts: [
      fact(1, {
        kind: "absence",
        absence: "no_dependency_traces",
        sentence: [
          { text: "No traces for " },
          { name: "checkout" },
          { text: "'s calls to other services in this window." },
        ],
      }),
      fact(2, {
        kind: "absence",
        absence: "could_not_read",
        read: "errors",
        sentence: [{ text: "Could not read recent errors this time." }],
      }),
    ],
  };
}
