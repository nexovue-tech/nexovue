import type { ReleaseMark } from "@/shared/deploy-markers";

import type { ContextFinding, ContextIncident, DashboardContext } from "./api";

/**
 * The "In this window" strip's order, as data (F20, FR47, FR67, FR70): open findings
 * first, then closed ones, then alert incidents, then releases, each kind newest first.
 */

/** Entries shown before "+N more" (*guess*, design). */
export const STRIP_SHOWN = 5;

export const FINDINGS_DIFFER =
  "Findings are judged from their own readings, so a number in a finding can differ slightly from this chart.";

export type StripEntry =
  | { kind: "finding"; key: string; finding: ContextFinding }
  | { kind: "incident"; key: string; incident: ContextIncident }
  | { kind: "release"; key: string; mark: ReleaseMark };

const newestFirst = (left: string, right: string) => Date.parse(right) - Date.parse(left);

export function stripEntries(context: DashboardContext): StripEntry[] {
  const open = context.findings.filter((finding) => finding.closed_at === null);
  const closed = context.findings.filter((finding) => finding.closed_at !== null);
  const byStart = (left: ContextFinding, right: ContextFinding) =>
    newestFirst(left.started_at, right.started_at);
  return [
    // Critical first among the open ones: the heaviest reason leads (design system).
    ...[...open]
      .sort(
        (left, right) =>
          Number(right.severity === "critical") - Number(left.severity === "critical") ||
          byStart(left, right),
      )
      .map((finding) => ({ kind: "finding" as const, key: `f-${finding.id}`, finding })),
    ...[...closed]
      .sort(byStart)
      .map((finding) => ({ kind: "finding" as const, key: `f-${finding.id}`, finding })),
    ...[...context.incidents]
      .sort((left, right) => newestFirst(left.opened_at, right.opened_at))
      .map((incident) => ({ kind: "incident" as const, key: `i-${incident.id}`, incident })),
    ...[...context.markers]
      .sort((left, right) => newestFirst(left.at, right.at))
      .map((mark) => ({
        kind: "release" as const,
        key: `r-${mark.deploy_id ?? mark.period_id ?? `${mark.service_name}:${mark.version}:${mark.at}`}`,
        mark,
      })),
  ];
}

/** Each cap the context hit, said in words, so a busy window is never read as complete. */
export function truncationWords(context: DashboardContext): string[] {
  const words: string[] = [];
  if (context.findings_truncated) words.push("Showing the newest 200 findings of this window.");
  if (context.incidents_truncated) {
    words.push("Showing the newest 50 alert incidents of this window.");
  }
  // F28's own words for its cap (FR18).
  if (context.markers_truncated) {
    words.push(`Showing the newest ${context.markers_cap} releases of this window.`);
  }
  return words;
}
