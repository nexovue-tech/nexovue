/**
 * How errors by version read (FR29 to FR31, FR51). Order, never cause: an error new in
 * this version is not proof the version brought it, and the caution says so beside the
 * list, never in a tooltip.
 */

import { plural } from "@/shared/format/plural";

import type { ErrorKind, VersionErrorGroup, VersionErrors } from "./api";

export const ERRORS_CAUTION =
  "Not seen in the previous version during this window. That is not proof this version introduced it. A rare error can look gone only because it has not recurred yet.";
export const ERRORS_TIMED_OUT = "This read took too long. Try the 1-hour window.";
export const ERRORS_MAY_BE_INCOMPLETE =
  "This project's sampling does not keep every error, so this list may be incomplete.";
export const FROM_THE_BROWSER = "from the browser";

const KIND_TITLES: Record<ErrorKind, string> = {
  new_here: "New here",
  gone_here: "Gone here",
  in_both: "In both",
  this_version: "In this version",
};
const KIND_ORDER: readonly ErrorKind[] = ["new_here", "gone_here", "in_both", "this_version"];

export function kindTitle(kind: ErrorKind): string {
  return KIND_TITLES[kind];
}

/** The groups under their headings, in the order the page lists them; empty ones left out. */
export function byKind(groups: VersionErrorGroup[]): Array<[ErrorKind, VersionErrorGroup[]]> {
  return KIND_ORDER.map(
    (kind) =>
      [kind, groups.filter((group) => group.kind === kind)] as [ErrorKind, VersionErrorGroup[]],
  ).filter(([, listed]) => listed.length > 0);
}

export function occurrenceWords(group: VersionErrorGroup): string {
  const mine = group.this_occurrences;
  const theirs = group.previous_occurrences;
  return group.kind === "this_version"
    ? `${mine} ${plural(mine, "time")}`
    : `${mine} ${plural(mine, "time")} here, ${theirs} before`;
}

/** Why the list is missing or partial, in words (FR30, FR51); null when it stands. */
export function errorsAbsence(errors: VersionErrors, agedOutWords: string | null): string | null {
  if (errors.timed_out) return ERRORS_TIMED_OUT;
  if (errors.aged_out_on !== null && agedOutWords !== null) {
    return `Error details aged out on ${agedOutWords}.`;
  }
  if (errors.groups.length === 0) return "No errors in either version during this window.";
  return null;
}
