import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/**
 * The feed's shape, and the reads its healthy state is made of.
 *
 * **Nothing produces findings.** The specification is explicit that it covers the
 * Findings *screen* and not the engine behind it, and the wireframe is explicit about
 * the consequence: until that engine exists, the healthy state is the entire screen.
 * `useFindings` therefore asks nothing and answers with an empty feed — deliberately,
 * and named so, rather than pointing at an endpoint that does not exist and rendering a
 * failed load to every customer.
 *
 * The feed itself is built and tested because its structure, ordering and actions are
 * committed (OD1, resolved 2026-09-13). Only the card's *contents* are provisional, so
 * that a drawing does not quietly become the specification for an engine nobody has
 * designed yet.
 */

export type FindingSeverity = "critical" | "warning";
export type FindingState = "active" | "acknowledged" | "resolved";

export interface Finding {
  id: string;
  /**
   * **The title is the product.** A finding is a sentence: subject, what changed, how
   * much, since when. F1 AC1.3 forbids raw metric names and percentile jargon here, and
   * that constraint belongs to the engine — a screen cannot rescue a title that arrives
   * as `http_server_duration_p99{service="checkout-api"} > 3.2`.
   */
  title: string;
  severity: FindingSeverity;
  state: FindingState;
  /** The entity the sentence is about: "checkout-api · POST /checkout". */
  subject: string;
  /** Scale, in the engine's own words: "~12% of checkout requests affected". */
  impact: string;
  /** Provisional, and the first thing an engine is likely to overrule. */
  probable_cause: string;
  started_at: string;
  resolved_at: string | null;
  /** A rollup behind the entity, drawn with the same sparkline as everywhere else. */
  trend: number[];
}

export interface FindingsResponse {
  findings: Finding[];
}

/**
 * The feed. Empty until an insight engine exists.
 *
 * Not a query against a missing endpoint: a 404 on every load would render the error
 * state, and the error state is the one thing on this screen that must never be shown
 * when it is not true.
 */
export function useFindings(_projectId: string | undefined, _timeWindow: TimeWindow) {
  return { findings: [] as Finding[], isPending: false, isError: false };
}

export interface ProjectTotals {
  service_count: number;
  error_count: number;
  requests_per_minute: number;
  /** A fraction, not a percentage: 0.05 is one call in twenty. */
  error_rate: number;
  p95_ms: number;
}

interface ServiceOverviewResponse {
  totals: ProjectTotals;
}

interface CheckSummary {
  /** Null when the check has not run in the window — never false, which means down. */
  up: boolean | null;
}

interface CheckListResponse {
  checks: CheckSummary[];
}

/**
 * The RED summary the healthy state carries.
 *
 * Declared here rather than imported from `features/overview`, because a feature never
 * imports another feature. What the two share is the endpoint and the token layer, which
 * is the right amount of sharing — not a type that would couple two screens' rendering
 * to one another.
 */
export function useProjectSummary(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: ["findings", "summary", projectId ?? "", timeWindow.from, timeWindow.to] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceOverviewResponse>("services/overview", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
    select: (response) => response.totals,
  });
}

/**
 * How many checks are passing, read only once the summary has landed.
 *
 * Gated for the reason the Overview strip is: the telemetry tier allows four reads in
 * flight and refuses the fifth rather than queueing it, so a screen that fires
 * everything at once fails part of itself every time it is opened.
 */
export function useCheckCounts(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  ready: boolean,
) {
  return useQuery({
    queryKey: ["findings", "checks", projectId ?? "", timeWindow.from, timeWindow.to] as const,
    enabled: projectId !== undefined && ready,
    queryFn: ({ signal }) =>
      apiClient.get<CheckListResponse>("synthetics/checks", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
    select: (response) => ({
      total: response.checks.length,
      passing: response.checks.filter((check) => check.up === true).length,
    }),
  });
}
