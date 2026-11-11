import type { CaseFilter } from "./api";

/**
 * The feed's filter (F22 FR22, design *The feed*): *All*, or one of three case
 * filters. It lives in the address (`?case=mine`), so back returns to it and it can
 * be linked (AC28, AC128); the router's `validateSearch` and the screen read it
 * through `parseFeedSearch`, so the two cannot disagree.
 */

export type FeedChoice = "all" | CaseFilter;

export const FEED_CHOICES: readonly { id: FeedChoice; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Active cases" },
  { id: "mine", label: "Assigned to me" },
  { id: "resolved", label: "Resolved cases" },
];

const CASE_FILTERS: ReadonlySet<string> = new Set<CaseFilter>(["active", "mine", "resolved"]);

export interface FeedSearch {
  case?: CaseFilter;
}

/** The address's filter; anything unknown is *All*, never an error page. */
export function parseFeedSearch(search: Record<string, unknown>): FeedSearch {
  const chosen = search.case;
  return typeof chosen === "string" && CASE_FILTERS.has(chosen)
    ? { case: chosen as CaseFilter }
    : {};
}

/** What the filter is called, for its own sentences and the back link. */
export function choiceLabel(choice: FeedChoice): string {
  return FEED_CHOICES.find((each) => each.id === choice)?.label ?? "All";
}

/** A finding's back link names the list it came from: "Back to Assigned to me". */
export function backLinkLabel(filter: CaseFilter | undefined): string {
  return filter === undefined ? "All findings" : `Back to ${choiceLabel(filter)}`;
}

/**
 * Each case filter's empty list in its own words (AC128). Never the RED summary: an
 * empty filter says nothing about the system's health.
 */
export const NO_CASES: Record<CaseFilter, string> = {
  active: "No active cases in your organization.",
  mine: "Nothing is assigned to you.",
  resolved: "No resolved cases are kept.",
};

/** The case filters read every project, so the selector above does not apply. */
export const ACROSS_PROJECTS =
  "Across all your projects. The project selector does not apply to this list.";
