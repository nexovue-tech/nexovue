import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

/**
 * What the dashboard reads.
 *
 * Every shape here is declared rather than imported from the feature that owns
 * the screen behind it: a feature never imports another feature, and what is
 * shared between them is the API's contract rather than that screen's code.
 * The cost is a few duplicated interfaces; the alternative is a dependency
 * between two screens that have no reason to know about each other.
 */

export interface ProjectTotals {
  service_count: number;
  request_count: number;
  error_count: number;
  requests_per_minute: number;
  /** A fraction, not a percentage: 0.05 is one call in twenty. */
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

export interface OverviewPoint {
  bucket: string;
  requests_per_minute: number;
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

interface ServiceOverviewResponse {
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  totals: ProjectTotals;
  points: OverviewPoint[];
}

export const overviewQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["services-overview", projectId, windowKeyOf(timeWindow)] as const;

/**
 * The project's RED, merged by the store.
 *
 * Not composed from `/services`: that list is capped, and a project's p95 is a
 * merge of every service's quantile state rather than an average of their
 * finished p95s — on real data the two differ by a factor of a hundred and
 * fifty. `test_services_live.py` holds that.
 */
export function useProjectOverview(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: overviewQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceOverviewResponse>("services/overview", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

/* ------------------------------------------------- the worst services below */

export interface OverviewService {
  service_name: string;
  requests_per_minute: number;
  error_rate: number;
  error_count: number;
  p95_ms: number;
}

interface ServiceListResponse {
  services: OverviewService[];
}

/** How many rows the dashboard shows. A dashboard is a glance, not a list. */
export const WORST_SERVICES_SHOWN = 5;

export const worstServicesQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["overview-worst-services", projectId, windowKeyOf(timeWindow)] as const;

/**
 * The few worst services, in the ranking the API already applies.
 *
 * Asked for with a small limit rather than trimmed after: the server ranks
 * worst-first, so the first five *are* the worst five, and asking for a hundred
 * to show five is a hundred rows across the wire for nothing.
 */
export function useWorstServices(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: worstServicesQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceListResponse>("services", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          limit: WORST_SERVICES_SHOWN,
        },
        signal,
      }),
    select: (response) => response.services,
  });
}

/* --------------------------------------------------- the strip of surfaces */

interface NodeListResponse {
  nodes: { kind: string; stale: boolean }[];
  truncated: boolean;
}

interface CheckListResponse {
  checks: { check_id: string; up: boolean | null }[];
}

interface WebVitalsResponse {
  vitals: { name: string; p75: number; readings: number }[];
}

export const surfacesQueryKey = (surface: string, projectId: string, timeWindow: TimeWindow) =>
  ["overview-surface", surface, projectId, windowKeyOf(timeWindow)] as const;

/**
 * The three surface reads behind the bottom strip.
 *
 * Each is the surface's own endpoint rather than a counting endpoint of its
 * own: a second way to count nodes is a second answer to "how many nodes", and
 * the two would eventually disagree. The infrastructure one is the expensive
 * member — a full node read for a count and a worst case — and is the first
 * thing to narrow if this screen is ever slow.
 *
 * **They wait for the panels.** `/services`, `/infra`, `/synthetics` and `/rum`
 * are all in the telemetry tier, which allows four requests in flight per
 * session (`ratelimit/policy.py`); a fifth is refused outright rather than
 * queued. This screen wants five, so firing them together made one of them a
 * guaranteed 429 on every cold load — and which one lost was a race.
 *
 * `ready` is what orders them: the panels are the screen, the strip is context
 * beneath it, and context can arrive a moment later. Two telemetry reads, then
 * three.
 */
export function useNodesSummary(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  ready: boolean,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: surfacesQueryKey("nodes", projectId ?? "", timeWindow),
    enabled: projectId !== undefined && ready,
    queryFn: ({ signal }) =>
      apiClient.get<NodeListResponse>("infrastructure/hosts", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

export function useChecksSummary(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  ready: boolean,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: surfacesQueryKey("checks", projectId ?? "", timeWindow),
    enabled: projectId !== undefined && ready,
    queryFn: ({ signal }) =>
      apiClient.get<CheckListResponse>("synthetics/checks", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

export function useVitalsSummary(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  ready: boolean,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: surfacesQueryKey("vitals", projectId ?? "", timeWindow),
    enabled: projectId !== undefined && ready,
    queryFn: ({ signal }) =>
      apiClient.get<WebVitalsResponse>("rum/web-vitals", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

/* ------------------------------------------------------ deploys, for IA3 */

export interface DeployEventSummary {
  version: string;
  service_name: string;
  deployed_at: string;
}

interface DeployEventListResponse {
  deploys: DeployEventSummary[];
}

export const overviewDeploysQueryKey = (projectId: string) =>
  ["overview-deploys", projectId] as const;

export function useProjectDeploys(projectId: string | undefined) {
  return useQuery({
    queryKey: overviewDeploysQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<DeployEventListResponse>("deploys", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.deploys,
  });
}
