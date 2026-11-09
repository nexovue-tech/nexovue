import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { DetectionStatus } from "@/shared/detection/headline";
import type { WindowChoice } from "@/shared/selection";
import { resolveChoice, windowCacheKey } from "@/shared/selection";

/**
 * Dashboards (F20): `/dashboards`, Postgres only, and `/dashboard-panels/read`, the batch
 * that reads a saved dashboard's panels in one charged request, with `/preview` for a
 * draft's. Shapes are the API's `dashboards/schemas.py` and `dashboardpanels/schemas.py`.
 *
 * The page reads two things on each refresh: the panels, and the panel context, which is
 * Postgres alone and so still answers when ClickHouse does not (D24).
 */

/**
 * The batch's whole budget: the server's 20 s deadline from arrival plus 5 s for
 * assembling and the network (`ratelimit/policy.py`, D25). The console's default 20 s
 * would throw away an answer the server was about to send.
 */
export const PANEL_READ_CLIENT_TIMEOUT_MS = 25_000;
const MS_PER_SECOND = 1000;

export type Signal = "rate" | "errors" | "latency";
export type PanelHeight = "small" | "medium" | "large";
export type ServiceRef = string | { picker: true };

/**
 * A stored panel as the API keeps it. Read loosely on purpose: a stored panel of a kind
 * this console no longer knows must still take its place and say so (FR8).
 */
export interface StoredPanel {
  id: string;
  kind: string;
  title: string | null;
  width: number;
  height: PanelHeight;
  settings: Record<string, unknown>;
}

export interface DashboardLink {
  label: string;
  url: string;
}

export interface Definition {
  schema_version: 1;
  refresh_seconds: 30 | 60 | 300 | null;
  default_range: string | null;
  service_picker: boolean;
  links: DashboardLink[];
  panels: StoredPanel[];
}

export interface DashboardDocument {
  title: string;
  description: string;
  definition: Definition;
}

export interface DashboardSummary {
  id: string;
  title: string;
  description: string;
  /** Null for a former member (FR38). */
  owner: string | null;
  panel_count: number;
  uses_picker: boolean;
  panels_not_reading: number;
  last_viewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Dashboard {
  id: string;
  title: string;
  description: string;
  definition: Definition;
  version: number;
  owner: string | null;
  updated_by: string | null;
  /** For delete's own sentence, "last opened 2 hours ago" (FR25). */
  last_viewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardTemplate {
  id: string;
  name: string;
  document: DashboardDocument;
}

export interface ContextFinding {
  id: string;
  scope: "service" | "project";
  service_name: string | null;
  severity: "critical" | "warning";
  title: string;
  started_at: string;
  closed_at: string | null;
}

export interface DashboardContext {
  project: {
    id: string;
    name: string;
    timezone: string;
    /** Null on a self-hosted install, which has no plan allowance. */
    retention: { raw_days: number; derived_days: number } | null;
  };
  window_start: string;
  window_end: string;
  detection: DetectionStatus;
  ever_reported: boolean;
  last_seen_at: string | null;
  findings: ContextFinding[];
  findings_truncated: boolean;
  open_findings: { total: number; by_service: Record<string, number> };
}

export interface Series {
  /** The reading's own name: `requests_per_minute`, `error_rate`, `p50_ms`, `p95_ms`, `p99_ms`. */
  label: string;
  points: [string, number][];
}

/** One panel's state. A `failed` panel carries no series, so it cannot be drawn as zeros (BR5). */
export type PanelResult =
  | {
      state: "ok";
      kind: string;
      read_at: string;
      bucket_seconds: number;
      data_starts_at: string | null;
      data_ends_at: string | null;
      series: Series[];
    }
  | { state: "empty"; kind: string; read_at: string; last_seen_at: string | null }
  | { state: "refused"; kind: string; retry_after_seconds: number }
  | {
      state: "not_in_project" | "needs_service" | "over_limit" | "timed_out" | "failed";
      kind: string;
    }
  | { state: "unknown_kind"; kind: "unknown_kind" };

export type PanelState = PanelResult["state"];

export interface PanelReadResponse {
  correlation_id: string | null;
  window_start: string;
  window_end: string;
  panels: Record<string, PanelResult>;
}

const dashboardsKey = ["dashboards"] as const;

export function useDashboards() {
  return useQuery({
    queryKey: dashboardsKey,
    queryFn: ({ signal }) =>
      apiClient.get<{ dashboards: DashboardSummary[] }>("dashboards", { signal }),
    select: (response) => response.dashboards,
  });
}

export function useDashboardTemplates() {
  return useQuery({
    queryKey: [...dashboardsKey, "templates"] as const,
    queryFn: ({ signal }) =>
      apiClient.get<{ templates: DashboardTemplate[] }>("dashboards/templates", { signal }),
    select: (response) => response.templates,
    // Fixed in the code: nothing a person does changes them.
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useDashboard(dashboardId: string) {
  return useQuery({
    queryKey: [...dashboardsKey, dashboardId] as const,
    queryFn: ({ signal }) => apiClient.get<Dashboard>(`dashboards/${dashboardId}`, { signal }),
  });
}

export function useCreateDashboard() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["dashboards", "create"],
    mutationFn: (document: DashboardDocument) => apiClient.post<Dashboard>("dashboards", document),
    onSuccess: () => client.invalidateQueries({ queryKey: dashboardsKey }),
  });
}

/** A save over a dashboard, naming the version it was read at: 409 if someone saved since (FR19). */
export function useReplaceDashboard(dashboardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["dashboards", "replace"],
    mutationFn: (body: DashboardDocument & { version: number }) =>
      apiClient.put<Dashboard>(`dashboards/${dashboardId}`, body),
    onSuccess: (saved) => {
      client.setQueryData([...dashboardsKey, dashboardId], saved);
      return client.invalidateQueries({ queryKey: dashboardsKey, exact: true });
    },
  });
}

/** Hidden for 30 days, restorable from the list (FR25, FR51). */
export function useDeleteDashboard(dashboardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["dashboards", "delete"],
    mutationFn: () => apiClient.delete<void>(`dashboards/${dashboardId}`),
    onSuccess: () => {
      client.removeQueries({ queryKey: [...dashboardsKey, dashboardId] });
      return client.invalidateQueries({ queryKey: dashboardsKey, exact: true });
    },
  });
}

/** A service as the services list names it: the raw name, bound and stored as it is. */
export interface ServiceChoice {
  service_name: string;
}

/** The most services the picker lists: the services route's own ceiling. */
const SERVICE_CHOICES_LIMIT = 500;

/**
 * The displayed project's services in the window, for the panel picker's real values
 * (FR23). Read from the Services screen's own endpoint: features do not import one another.
 */
export function useServiceChoices(projectId: string | undefined, choice: WindowChoice) {
  return useQuery({
    queryKey: ["dashboard-service-choices", projectId, windowCacheKey(choice)] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) => {
      const window = resolveChoice(choice);
      return apiClient.get<{ services: ServiceChoice[] }>("services", {
        searchParams: {
          project_id: projectId,
          from: window.from,
          to: window.to,
          limit: SERVICE_CHOICES_LIMIT,
        },
        signal,
      });
    },
    select: (response) => response.services.map((service) => service.service_name),
  });
}

/** One Apply: the draft as it stands, and which panel it reads. */
export interface PreviewAsk {
  definition: Definition;
  panelIds: string[] | undefined;
  /** Counts each Apply, so pressing it twice reads twice and typing reads never (AC135). */
  applied: number;
}

/**
 * A draft's panels through `POST /dashboard-panels/preview`, charged as a read (FR24).
 * A query, not a mutation, so a refusal and an outage are said in the page's own words
 * with no toast, as the saved read's are (`meta`, AC145). It reads only when asked.
 */
export function usePanelPreview(
  ask: PreviewAsk | undefined,
  projectId: string | undefined,
  choice: WindowChoice,
  service: string | undefined,
) {
  return useQuery({
    // The draft itself is in the key: two sheets each count their Applies from one, and
    // must never be served each other's answer.
    queryKey: [
      "dashboard-preview",
      projectId,
      windowCacheKey(choice),
      service,
      ask?.applied,
      ask?.panelIds,
      ask ? JSON.stringify(ask.definition) : undefined,
    ] as const,
    enabled: ask !== undefined && projectId !== undefined,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    meta: { ownWords: ["unavailable", "rate-limited"] },
    queryFn: ({ signal }) =>
      apiClient.post<PanelReadResponse>(
        "dashboard-panels/preview",
        {
          project_id: projectId,
          ...windowParams(choice),
          service,
          panels: ask?.panelIds,
          definition: ask?.definition,
        },
        { timeoutMs: PANEL_READ_CLIENT_TIMEOUT_MS, signal },
      ),
  });
}

/** A live range by its id, which the server rounds once for everyone; fixed times as they are. */
function windowParams(choice: WindowChoice): Record<string, string> {
  return choice.kind === "relative"
    ? { range: choice.rangeId }
    : { from: choice.from, to: choice.to };
}

/** The dashboard's own refresh, never the console's 5 or 10 seconds; a fixed window never refreshes. */
export function refreshIntervalOf(definition: Definition, choice: WindowChoice): number | false {
  if (choice.kind === "absolute" || definition.refresh_seconds === null) return false;
  return definition.refresh_seconds * MS_PER_SECOND;
}

export function useDashboardContext(
  dashboard: Dashboard | undefined,
  projectId: string | undefined,
  choice: WindowChoice,
) {
  const interval = dashboard ? refreshIntervalOf(dashboard.definition, choice) : false;
  return useQuery({
    queryKey: ["dashboard-context", dashboard?.id, projectId, windowCacheKey(choice)] as const,
    enabled: dashboard !== undefined && projectId !== undefined,
    refetchInterval: interval,
    queryFn: ({ signal }) =>
      apiClient.get<DashboardContext>(`dashboards/${dashboard?.id}/context`, {
        searchParams: { project_id: projectId, ...windowParams(choice) },
        signal,
      }),
  });
}

export function useDashboardPanels(
  dashboard: Dashboard | undefined,
  projectId: string | undefined,
  choice: WindowChoice,
) {
  const interval = dashboard ? refreshIntervalOf(dashboard.definition, choice) : false;
  return useQuery({
    // The choice and not the instants: the key stays still while a live window moves, so
    // a refresh never brings the skeleton back (AC29).
    queryKey: [
      "dashboard-panels",
      dashboard?.id,
      dashboard?.version,
      projectId,
      windowCacheKey(choice),
    ] as const,
    enabled: dashboard !== undefined && projectId !== undefined,
    refetchInterval: interval,
    // Back on the tab, it reads again only once past its own interval.
    staleTime: interval === false ? Number.POSITIVE_INFINITY : interval,
    // Each refresh is the retry: a cold ClickHouse's 503 must not be read three times a
    // minute on a wall display (AC61).
    retry: false,
    // Said on the page in its own words, so the console raises no toast (AC61, AC145).
    meta: { ownWords: ["unavailable", "rate-limited"] },
    queryFn: ({ signal }) =>
      apiClient.get<PanelReadResponse>("dashboard-panels/read", {
        searchParams: {
          dashboard_id: dashboard?.id,
          project_id: projectId,
          ...windowParams(choice),
        },
        timeoutMs: PANEL_READ_CLIENT_TIMEOUT_MS,
        signal,
      }),
  });
}
