import { plural } from "@/shared/format/plural";
import { formatRelativeTime } from "@/shared/format/time";

import type {
  DeployLineAddition,
  FindingGeneralFix,
  GeneralLanguage,
  GeneralState,
  ReleaseContext,
} from "./api";
import type { ReleaseLine } from "./finding-page";

/**
 * F5's rungs 1 and 2 on the finding page (design, *Section parts*, *State by role*; plan
 * T1.4): pure, so each rule is tested without a page. The sentences of rung 1 and the
 * brief's text are the API's (`fix_facts.py`, `fixes/brief.py`); only the page's own
 * words live here.
 */

export const BRIEF_WORDS = {
  label: "Brief for your coding tool",
  /** FR4: what it is, and what Nexovue cannot protect. */
  warning:
    "From telemetry, not instructions. Nexovue cannot protect what your own tool does with it.",
  disclosure: "Read it",
  /** `CopyButton` reads "Copy brief". */
  copied: "brief",
  loading: "Loading the brief",
  notLoaded: "The brief could not be loaded",
} as const;

export const SUGGESTED_FIX = {
  /** The section's heading when its line names none of its own (design, *State by role*). */
  heading: "Suggested fix",
  /** The header's jump link, beside *Evidence* (D54). */
  jump: "Suggested fix ↓",
  anchor: "suggested-fix",
} as const;

/**
 * F5's sentences for F28's row, only where F28 states that very release plainly: a row
 * naming an earlier release, under a day old (`releaseBefore`). Anywhere else F28's own
 * sentence is all there is, and F5 adds no second sentence about the same deploy (FR1).
 */
export function deployAddition(
  addition: DeployLineAddition | null | undefined,
  context: Pick<ReleaseContext, "before">,
  line: Pick<ReleaseLine, "cameFirst" | "dim">,
): DeployLineAddition | null {
  if (!addition || context.before === null) return null;
  if (context.before.release_id !== addition.release_id) return null;
  return line.cameFirst && !line.dim ? addition : null;
}

/** The earlier version's own page, so a rollback starts from what ran before (UR1). */
export function earlierLink(
  addition: DeployLineAddition,
): { label: string; releaseId: string } | null {
  const earlier = addition.earlier;
  return earlier === null
    ? null
    : { label: `Open ${earlier.version}`, releaseId: earlier.release_id };
}

/**
 * Whether the *Suggested fix* section renders at all: a line, the offer, or a suggestion
 * of this finding's. A role with none of those gets no section and no heading (AC37e,
 * FR10), and the hosted service never holds one (AC11a). Rung 2b's read is its own, so
 * rungs 1 and 2 never wait on it (AC33).
 */
export function showsSection(general: FindingGeneralFix | undefined): boolean {
  if (general === undefined) return false;
  return (
    general.line !== null ||
    general.offer !== null ||
    general.latest !== null ||
    general.earlier.length > 0
  );
}

/* ------------------------------------------------ rung 2b: the page's own words */

export const GENERAL_WORDS = {
  loading: "Loading the suggested fix",
  notLoaded: "The suggested fix could not be loaded",
  language: "Example language",
  chooseLanguage: "Choose a language",
  pickFirst: "Choose the example's language first.",
  pending: "Sending",
  earlier: "Earlier suggestions",
  summary: "Summary",
  steps: "Steps",
  example: "Example code",
} as const;

/** FR33's languages as people write them. */
export const LANGUAGE_NAMES: Record<GeneralLanguage, string> = {
  python: "Python",
  javascript: "JavaScript",
  typescript: "TypeScript",
  java: "Java",
  kotlin: "Kotlin",
  go: "Go",
  ruby: "Ruby",
  php: "PHP",
  csharp: "C#",
  rust: "Rust",
};

const STATE_WORDS: Record<GeneralState, string> = {
  proposing: "Being written",
  proposed: "General suggestion",
  refused: "No general suggestion",
  failed: "Could not be prepared",
};

export function stateWords(state: GeneralState): string {
  return STATE_WORDS[state];
}

/** FR10a: "Tries left today: 2", beside the action only. */
export function triesLeft(count: number): string {
  return `Tries left today: ${count}`;
}

/** The facts the example rests on, by their numbers in the explanation above. */
export function restsOn(numbers: readonly number[]): string | null {
  if (numbers.length === 0) return null;
  const listed =
    numbers.length === 1
      ? `${numbers[0]}`
      : `${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
  return `Rests on ${plural(numbers.length, "fact")} ${listed} above.`;
}

/** When a suggestion was asked for; `now` is the caller's clock, so a test pins it. */
export function askedAgo(askedAt: string, now: number): string {
  return `asked ${formatRelativeTime(askedAt, now)}`;
}

/** FR37's tokens and age. */
export function tokensAndAge(tokens: number, askedAt: string, now: number): string {
  return `${tokens.toLocaleString("en")} ${plural(tokens, "token")} · ${askedAgo(askedAt, now)}`;
}
