import { plural } from "@/shared/format/plural";
import { formatRelativeTime } from "@/shared/format/time";

import type {
  DeployLineAddition,
  FindingGeneralFix,
  FindingSourceFix,
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

/** Rung 3 shows when it has an offer, a suggestion, earlier ones, or a line of its own. */
export function showsSource(source: FindingSourceFix): boolean {
  return (
    source.offer !== null ||
    source.latest !== null ||
    source.line !== null ||
    source.earlier.length > 0
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

/* ------------------------------------------- rung 3: the page's own words (Step 3) */

export const SOURCE_WORDS = {
  notLoaded: "The suggested fix could not be loaded",
  downloadPatch: "Download patch",
  pathLabel: (index: number) => `File path ${index + 1}`,
  addPath: "Name another file",
  email: "Email me when this needs me",
  triesLeft: (count: number) => `Tries left today: ${count}`,
  link: "Connect your GitHub account",
  remove: "Remove",
  read: "Read the list again",
  cancel: "Cancel",
  leftOut: "Left out",
  unmatched: (count: number) =>
    `${count} ${plural(count, "name", "names")} from the error matched no file.`,
  bytes: (count: number) => `${count.toLocaleString("en-GB")} bytes`,
  linesSent: (lines: readonly number[]) => `lines ${lines[0]} to ${lines[1]} sent`,
  moved: "This file has changed on the default branch since the running version.",
  hint: "What should it look at? (optional)",
  confirmTop: "Send without the file at the top of the stack",
  lapses: (minutes: number) =>
    minutes <= 0
      ? "This list lapses now."
      : `This list waits ${minutes} more ${plural(minutes, "minute", "minutes")}.`,
  askAgain: "Ask again",
} as const;

/** Whole minutes left before a gathered list lapses (FR13a), never below zero. */
export function minutesLeft(lapsesAt: string, now: number): number {
  return Math.max(0, Math.ceil((Date.parse(lapsesAt) - now) / 60_000));
}

/** `document.title`'s word while a person waits on a background tab (design, *UX*). */
export function sourceTitle(state: string | undefined): string | null {
  if (state === "gathered") return "Files ready: confirm";
  if (state === "proposed") return "Suggestion ready";
  if (state === "opened") return "Draft pull request open";
  return null;
}

/* ------------------------------------------------ rung 5: the draft pull request (Step 7) */

/** The few words the draft's controls say themselves; every sentence is the API's. */
export const DRAFT_WORDS = {
  cancel: "Not now",
  deleteAsked: "Asked GitHub to delete this branch.",
} as const;

/* ------------------------------------------- rung 3: the written suggestion (Step 4) */

export const DIFF_WORDS = {
  flags: "What to look at first",
  flagAtLine: (label: string, path: string, line: number) => `${label}: ${path}, line ${line}`,
  flagInFile: (label: string, path: string) => `${label}: ${path}`,
  counts: (added: number, removed: number) => `+${added} −${removed}`,
  marker: (marker: " " | "-" | "+") =>
    marker === "+" ? "added" : marker === "-" ? "removed" : "unchanged",
  escaped: "A character that cannot be seen, shown as its code",
  filesRead: "Files read",
  leftOut: "Left out",
  notChecked: "What was not checked",
  written: (age: string, commit: string, tokens: number) =>
    `Written ${age}, from ${commit || "an unknown commit"}. ${tokens.toLocaleString("en")} ${plural(tokens, "token")} used.`,
  modelWords: "The model's words, not checked by Nexovue",
  refusedAsked: (path: string, why: string) => `${path}: ${why}`,
} as const;

/** A diff line's anchor, so a flag can jump to it. Built from Nexovue's own values. */
export function flagAnchor(path: string, side: "new" | "old" | "file", line: number): string {
  return `fix-line-${side}-${line}-${path.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

export interface TextPiece {
  text: string;
  escaped: boolean;
}

// Control characters (but tab), bidirectional controls, zero-width marks and the tag block:
// shown as their codes in every line, so nothing in a suggestion hides or reorders text.
const HIDDEN_RANGES: readonly (readonly [number, number])[] = [
  [0x0000, 0x0008],
  [0x000b, 0x001f],
  [0x007f, 0x009f],
  [0x200b, 0x200f],
  [0x2028, 0x202e],
  [0x2060, 0x2069],
  [0xfeff, 0xfeff],
  [0xe0000, 0xe007f],
];

function hidden(code: number): boolean {
  return HIDDEN_RANGES.some(([low, high]) => low <= code && code <= high);
}

/** A line as text pieces, each hidden character replaced by its visible code (AC36). */
export function visibleEscapes(line: string): TextPiece[] {
  const pieces: TextPiece[] = [];
  let plain = "";
  for (const character of line) {
    const code = character.codePointAt(0) ?? 0;
    if (!hidden(code)) {
      plain += character;
      continue;
    }
    if (plain) pieces.push({ text: plain, escaped: false });
    plain = "";
    pieces.push({ text: `\\u{${code.toString(16).toUpperCase()}}`, escaped: true });
  }
  if (plain) pieces.push({ text: plain, escaped: false });
  return pieces;
}
