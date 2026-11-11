import { ApiError, isConflict } from "@/shared/api-client";
import { formatZonedTime } from "@/shared/format/zoned";

import type {
  ExplanationBasis,
  ExplanationFact,
  ExplanationKind,
  ExplanationRating,
  ExplanationSegment,
  FindingDetail,
  FindingExplanation,
  RatingReason,
} from "./api";

/**
 * A finding's explanation as words, pure (F3 design, *Words, recorded once*; spec,
 * *States*). The section renders what this returns, so every state's words are tested
 * here, as `finding-page.ts`'s are.
 *
 * **Nothing here names a cause the API did not name** (FR50): a held cause reads "The
 * facts that line up with this are below", never "Not enough evidence", which would be
 * false. A failed read is never "Not enough evidence" either (AC87).
 */

/** The section's heading (A69). */
export const SECTION_HEADING = "What lines up with this";

export const WORDS = {
  reading: "Reading the facts",
  stillReading: "Still reading the facts",
  busy: "Nexovue is busy with other findings.",
  notRead: "The facts could not be read",
  notLoaded: "The explanation could not be loaded",
  nothingFound: "Not enough evidence to name a likely cause",
  failedReads: "Could not finish reading the facts, so no likely cause yet",
  held: "The facts that line up with this are below",
  viewerRetry: "Someone on the team can retry this.",
  readAgain: "Read the facts again",
  signedTimingOnly: "The deploy is a signed record; the link to this finding is timing only.",
} as const;

/** After this long unread, the header says it is still reading (spec, *States*). */
export const STILL_READING_AFTER_MS = 5 * 60_000;
/** The facts shown before "Show all N facts" (FR44; a guess). */
export const FIRST_FACTS = 5;

export type HeaderKind =
  | "loading"
  | "not_loaded"
  | "reading"
  | "still_reading"
  | "not_read"
  | "cause"
  | "held"
  | "failed_reads"
  | "nothing_found";

/** The one line under the title (FR14): always present, its space always held (AC86). */
export interface HeaderLine {
  kind: HeaderKind;
  /** The cause's own segments when it has one; otherwise one text segment. */
  segments: ExplanationSegment[];
}

export function headerLine(
  explanation: FindingExplanation | undefined,
  {
    failed,
    finding,
    now = Date.now(),
  }: { failed: boolean; finding: Pick<FindingDetail, "started_at">; now?: number },
): HeaderLine {
  const line = (kind: HeaderKind, text: string): HeaderLine => ({ kind, segments: [{ text }] });
  if (explanation === undefined) {
    return failed ? line("not_loaded", WORDS.notLoaded) : line("loading", WORDS.reading);
  }
  if (explanation.state === "reading") {
    const late = now - Date.parse(finding.started_at) >= STILL_READING_AFTER_MS;
    return late ? line("still_reading", WORDS.stillReading) : line("reading", WORDS.reading);
  }
  if (explanation.state === "not_read") return line("not_read", WORDS.notRead);
  if (explanation.likely_cause !== null && explanation.likely_cause.header.length > 0) {
    return { kind: "cause", segments: explanation.likely_cause.header };
  }
  if (explanation.cause_held) return line("held", WORDS.held);
  if (explanation.reads_failed > 0) return line("failed_reads", WORDS.failedReads);
  return line("nothing_found", WORDS.nothingFound);
}

/** The words a basis label reads (design, *Words*), with the kind for the subtler ones. */
export function basisLabel(fact: Pick<ExplanationFact, "basis" | "kind">): string | null {
  const labels: Record<ExplanationBasis, string> = {
    verified: fact.kind === "deploy" ? "Signed deploy record" : "nexovue's own check",
    telemetry: "Counted from telemetry",
    telemetry_text:
      fact.kind === "deploy" ? "Version seen in telemetry" : "Read from telemetry text",
  };
  if (fact.basis === null) return null;
  if (fact.basis === "verified" && fact.kind === "infrastructure") {
    return "This install's intake record";
  }
  return labels[fact.basis];
}

/** Setup absences a person can fix (FR45); a failed read never is one. */
const SETUP_ABSENCES = new Set(["no_deploy_information", "no_dependency_traces"]);

/**
 * The fact list as the page shows it (FR44): F28's release rows are the first row, not
 * repeated here; repeated setup absences collapse to one; a failed read never collapses.
 */
export function listedFacts(facts: readonly ExplanationFact[]): ExplanationFact[] {
  const seenSetup = new Set<string>();
  const listed: ExplanationFact[] = [];
  for (const fact of facts) {
    if (fact.role !== null) continue;
    if (fact.kind === "absence" && fact.absence !== null && SETUP_ABSENCES.has(fact.absence)) {
      if (seenSetup.has(fact.absence)) continue;
      seenSetup.add(fact.absence);
    }
    listed.push(fact);
  }
  return listed;
}

export interface SetupStep {
  label: string;
  doc: string;
}

/**
 * FR45: with no cause and every read complete, the one setup step that would add the
 * most evidence. None after a failed read (AC87), and none with a cause.
 */
export function setupStep(explanation: FindingExplanation | undefined): SetupStep | null {
  if (explanation === undefined || explanation.state !== "read") return null;
  if (explanation.likely_cause !== null || explanation.cause_held) return null;
  if (explanation.reads_failed > 0) return null;
  const absences = new Set(explanation.facts.map((fact) => fact.absence));
  if (absences.has("no_deploy_information")) {
    return { label: "Send deploy notifications", doc: "deploys" };
  }
  if (absences.has("no_dependency_traces")) {
    return { label: "Send traces from the services this one calls", doc: "index" };
  }
  return null;
}

/** "Read at 14:08 IST", with the shared-cause count when there is one (FR47). */
export function readLine(explanation: FindingExplanation, zone?: string): string | null {
  if (explanation.read_at === null) return null;
  const read = `Read at ${formatZonedTime(explanation.read_at, { zone })}`;
  const shared = explanation.shared_cause_count;
  if (explanation.likely_cause === null || shared < 2) return read;
  return `${read} · ${shared} open findings name this likely cause`;
}

/** AC88: a refetch failed while facts are held. */
export function staleLine(explanation: FindingExplanation, zone?: string): string {
  const read =
    explanation.read_at === null
      ? ""
      : `Read at ${formatZonedTime(explanation.read_at, { zone })}. `;
  return `${read}Could not refresh; still trying`;
}

/** FR41, AC80: why "Read the facts again" is disabled, or null when it is not. */
export function readAgainWait(
  nextReadAt: string | null,
  readAt: string | null,
  { zone, now = Date.now() }: { zone?: string; now?: number } = {},
): string | null {
  if (nextReadAt === null || Date.parse(nextReadAt) <= now) return null;
  const read = readAt === null ? "" : `Read at ${formatZonedTime(readAt, { zone, now })}. `;
  return `${read}You can read again at ${formatZonedTime(nextReadAt, { zone, now })}`;
}

/** A refused read-again's next time, from the 409's problem (AC80). */
export function refusedUntil(error: unknown): string | null {
  if (!isConflict(error) || !(error instanceof ApiError)) return null;
  const next = error.problem?.next_read_at;
  return typeof next === "string" ? next : null;
}

/** FR19's carried-read note: a fact kept from an earlier read because this one failed. */
export function carriedNote(fact: ExplanationFact, zone?: string): string | null {
  if (fact.read_at === null) return null;
  return `As read at ${formatZonedTime(fact.read_at, { zone })}`;
}

/** What a page left open announces (AC69): a new version, or a changed cause. */
export function changeAnnouncement(
  before: FindingExplanation | undefined,
  after: FindingExplanation | undefined,
): string | null {
  if (before === undefined || after === undefined) return null;
  if (before.version === after.version) return null;
  const was = before.likely_cause?.evidence_id ?? null;
  const now = after.likely_cause?.evidence_id ?? null;
  if (was !== now) {
    return after.likely_cause === null
      ? "The facts were read again; no likely cause is named now."
      : `The facts were read again; the likely cause changed: ${plainText(after.likely_cause.header)}`;
  }
  return "The facts were read again.";
}

/** A sentence's words with names in place, for announcements only, never for markup. */
export function plainText(segments: readonly ExplanationSegment[], zone?: string): string {
  return segments
    .map((segment) =>
      segment.at ? formatZonedTime(segment.at, { zone }) : (segment.name ?? segment.text ?? ""),
    )
    .join("");
}

/** The kinds the section names, for an accessible label on a fact. */
export const KIND_WORDS: Record<ExplanationKind, string> = {
  deploy: "Deploy",
  error_group: "Error",
  dependency: "Dependency",
  check: "Check",
  incident: "Linked alert",
  other_finding: "Other finding",
  traffic: "Traffic",
  infrastructure: "Infrastructure",
  absence: "Missing",
};

/** FR20's words (design, *Words, recorded once*). */
export const RATING_WORDS = {
  question: "Was this useful?",
  yes: "Yes",
  no: "No",
  reasons: {
    wrong_cause: "The likely cause is wrong",
    not_useful: "It did not help",
    bad_wording: "The AI wording is poor",
  } satisfies Record<RatingReason, string>,
  thanks: "Thanks, recorded.",
} as const;

/** What a person sees after rating: what they said, carried to later versions (AC83). */
export function ratingLine(rating: ExplanationRating): string {
  const said = rating.helpful
    ? "you said it was useful"
    : `you said: ${RATING_WORDS.reasons[rating.reason ?? "not_useful"]}`;
  return `${RATING_WORDS.thanks} ${said[0].toUpperCase()}${said.slice(1)}.`;
}
