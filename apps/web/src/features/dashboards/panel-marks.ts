import type { DeployMarker } from "@/shared/deploy-markers";
import { releaseMarkers } from "@/shared/deploy-markers";
import { findingSpans } from "@/shared/finding-spans";

import type { DashboardContext, PanelResult, StoredPanel } from "./api";
import { PROJECT_MARKED_KINDS, serviceOf, TIME_KINDS } from "./panel-words";
import type { Marks } from "./time-lines";
import { NO_MARKS } from "./time-lines";

/**
 * Which findings and releases a time panel draws (F20, FR18, T5.5), worked out as data.
 *
 * - A service's chart draws its service's marks and the project-wide findings.
 * - A top-N chart draws those of the services it lists.
 * - A project chart draws them all.
 * - A node, check or statement chart draws all of the project's, and says so, so nobody
 *   reads a checkout finding on a disk chart as a claim about that disk (AC132).
 *
 * A `seen` mark keeps its basis, so it is drawn dotted and never called a deploy (AC122).
 */

export const ALL_IN_PROJECT = "All findings and releases in this project.";
export const NO_DEPLOY_INFORMATION = "We receive no deploy information for this service.";

export interface PanelMarking {
  marks: Marks;
  /** "All findings and releases in this project", for a panel with no service. */
  note?: string;
  /** F28's words for a service whose coverage is `none` (FR18, AC66). */
  coverage?: string;
}

/** The services a time panel's marks belong to, or undefined for the whole project's. */
function servicesOf(
  panel: StoredPanel,
  reading: PanelResult | undefined,
  picked: string | undefined,
): readonly string[] | undefined {
  if (panel.kind === "service_trend" || panel.kind === "endpoint_trend") {
    const service = serviceOf(panel) ?? picked;
    return service === undefined ? [] : [service];
  }
  if (panel.kind === "top_services_trend") {
    if (reading?.state !== "ok") return [];
    return reading.series.flatMap((line) => (line.service ? [line.service] : []));
  }
  return undefined;
}

export function marksFor(
  panel: StoredPanel,
  context: DashboardContext | undefined,
  reading?: PanelResult,
  picked?: string,
): PanelMarking {
  if (!context || !TIME_KINDS.has(panel.kind)) return { marks: NO_MARKS };
  const window = { from: context.window_start, to: context.window_end };
  const services = servicesOf(panel, reading, picked);
  const named = services === undefined ? undefined : new Set(services);
  const findings = context.findings.filter(
    (finding) =>
      named === undefined ||
      finding.scope === "project" ||
      (finding.service_name !== null && named.has(finding.service_name)),
  );
  const markers: DeployMarker[] = releaseMarkers(context.markers, window).filter(
    (marker) => named === undefined || named.has(marker.service),
  );
  const marking: PanelMarking = {
    marks: {
      spans: findingSpans(
        findings.map((finding) => ({ ...finding, title: finding.title })),
        window,
      ),
      markers,
    },
  };
  if (PROJECT_MARKED_KINDS.has(panel.kind)) marking.note = ALL_IN_PROJECT;
  const [only] = services ?? [];
  if (services?.length === 1 && only !== undefined && context.coverage[only] === "none") {
    marking.coverage = NO_DEPLOY_INFORMATION;
  }
  return marking;
}
