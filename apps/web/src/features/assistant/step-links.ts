import { formatZonedTime } from "@/shared/format/zoned";

import type { StepLink, StepView } from "./api";
import { agedOut, latestNoTimeFilter, NO_ROWS, READ_REFUSED_FOR_NOW } from "./states";

/**
 * Where a step may lead: the screens the catalog's destination table names (design, *Where
 * each step leads*; `test_assistant_catalog.py` holds the catalog to it). The API builds the
 * link; a path outside this list is shown as no link at all.
 */
export const STEP_TARGETS = [
  "/",
  "/findings/$findingId",
  "/services",
  "/services/$name",
  "/service-map",
  "/releases",
  "/releases/$releaseId",
  "/alerts",
  "/alerts/incidents/$incidentId",
  "/synthetics",
  "/errors",
  "/logs",
  "/traces",
  "/traces/$traceId",
  "/metrics",
  "/infrastructure",
] as const;
export type StepTarget = (typeof STEP_TARGETS)[number];

const PARAM = /\$([A-Za-z]+)/g;

/** The link's route, when it is one of the catalog's and every parameter it names is given. */
export function stepTarget(link: StepLink): StepTarget | null {
  if (!(STEP_TARGETS as readonly string[]).includes(link.path)) return null;
  const named = [...link.path.matchAll(PARAM)].map((match) => match[1] ?? "");
  return named.every((name) => typeof link.params?.[name] === "string" && link.params[name] !== "")
    ? (link.path as StepTarget)
    : null;
}

/** The link as an address a colleague can open: path, filled, and its search. */
export function stepHref(link: StepLink, origin: string): string | null {
  if (stepTarget(link) === null) return null;
  const path = link.path.replace(PARAM, (_, name: string) =>
    encodeURIComponent(link.params[name] ?? ""),
  );
  const search = new URLSearchParams(link.query).toString();
  return `${origin}${path}${search ? `?${search}` : ""}`;
}

/** The step's range in the person's zone, or null for a step that read no window. */
export function stepRange(step: StepView, zone: string | undefined, now: number): string | null {
  if (!step.from_time || !step.to_time) return null;
  return `${formatZonedTime(step.from_time, { zone, now })} to ${formatZonedTime(step.to_time, { zone, now })}`;
}

/**
 * What a step read, in nexovue's words (spec, *Error cases*): rows, cut, nothing matched,
 * aged out, or what failed. Null while it runs.
 */
export function stepOutcome(step: StepView): string | null {
  if (step.state === "aged_out") {
    return step.kept_since ? agedOut(step.kept_since) : agedOut("this range");
  }
  if (step.state === "read") {
    const rows = step.row_count ?? 0;
    if (rows === 0) return `${NO_ROWS}.`;
    const read = step.from_time ? `${rows} rows read` : latestNoTimeFilter(rows);
    const cut = step.was_cut ? `. Only the first ${rows} rows were read` : "";
    const kept = step.kept_since ? ` ${agedOut(step.kept_since)}` : "";
    return `${read}${cut}.${kept}`;
  }
  if (step.state === "failed" && step.failure === "limited") return READ_REFUSED_FOR_NOW;
  if (step.state === "failed" || step.state === "refused") {
    return `This read ${step.state === "refused" ? "was refused" : "failed"}${
      step.failure ? ` (${step.failure.replaceAll("_", " ")})` : ""
    }.`;
  }
  return null;
}

/**
 * **Copy steps** (FR38, AC93): each step's own words, range, outcome and link, all built by
 * nexovue. No model word: not the answer, not the question, not the phrase a model searched
 * for (a link carries it only where its checks let it be shown).
 */
export function stepsAsText(
  steps: readonly StepView[],
  { zone, now, origin }: { zone: string | undefined; now: number; origin: string },
): string {
  return steps
    .map((step) => {
      const parts = [`${step.key}. ${step.words}`];
      const range = stepRange(step, zone, now);
      if (range) parts.push(range);
      const outcome = stepOutcome(step);
      if (outcome) parts.push(outcome);
      const href = step.link ? stepHref(step.link, origin) : null;
      if (href) parts.push(href);
      if (step.link?.caveat) parts.push(step.link.caveat);
      return parts.join(" · ");
    })
    .join("\n");
}
