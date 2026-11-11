import type { DeployLineAddition, FindingFixes, ReleaseContext } from "./api";
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
 * Whether the *Suggested fix* section renders at all. A role with no line gets no
 * section and no heading (AC37e), and the hosted service never holds one (AC11a).
 */
export function showsSection(fixes: FindingFixes | undefined): boolean {
  return fixes?.section != null;
}
