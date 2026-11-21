import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiError } from "@/shared/api-client";
import { apiClient, isRateLimited } from "@/shared/api-client";
import type { ReleaseMark } from "@/shared/deploy-markers";
import type { DetectionStatus } from "@/shared/detection/headline";
import { DASHBOARD_REFRESH_FLOOR_MS } from "@/shared/refresh";
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
  /** Set on Recently deleted only: what the 30 days for restoring count from. */
  deleted_at: string | null;
}

export interface DashboardList {
  dashboards: DashboardSummary[];
  /** Only `?service=` limits a list, and then more may exist than were listed (FR57). */
  more: boolean;
}

/** One entry of a dashboard's history (FR51); `current` is the version in use. */
export interface VersionSummary {
  version: number;
  title: string;
  saved_by: string | null;
  saved_at: string;
  current: boolean;
}

export interface DashboardVersion extends DashboardDocument {
  version: number;
  saved_by: string | null;
  saved_at: string;
  current: boolean;
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

/** An alert incident open during the window whose rule names a service (FR67). */
export interface ContextIncident {
  id: string;
  service_name: string;
  rule_name: string;
  severity: "critical" | "warning";
  opened_at: string;
  resolved_at: string | null;
}

/** F28's coverage of a service: whether it reports deploys, versions, both or neither. */
export type Coverage = "deploys_and_versions" | "deploys_only" | "versions_only" | "none";

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
  /** F28's marks: a `reported` deploy is signed by CI; a `seen` version is not a deploy (AC122). */
  markers: ReleaseMark[];
  markers_truncated: boolean;
  markers_cap: number;
  coverage: Record<string, Coverage>;
  incidents: ContextIncident[];
  incidents_truncated: boolean;
}

export interface Series {
  /**
   * The reading's own name: `requests_per_minute`, `error_rate`, `p50_ms`, `p95_ms`,
   * `p99_ms`, or a node chart's metric name.
   */
  label: string;
  /** A top-N chart's line: the service it is, drawn as a link to that service (FR35). */
  service?: string | null;
  /** A top-nodes chart's line: the node it is. */
  node?: NodeName | null;
  /**
   * A chosen metric series' attributes, its label at read time. Customer text: shown only
   * through the visible escapes (FR61).
   */
  attributes?: MetricAttribute[] | null;
  points: [string, number][];
}

/** One attribute of a metric series, its key a dotted path as the SDK sent it. */
export interface MetricAttribute {
  key: string;
  value: string;
}

export interface NodeName {
  host: string;
  container: string;
  reporter: string;
  reporter_instance: string;
}

/**
 * A number panel's value, and the previous window's when compared. `previous` is null
 * unless the data covers that window: "Not enough history to compare" (AC73).
 */
export interface NumberReading {
  measure: string;
  value: number;
  previous?: number | null;
  previous_coverage?: number | null;
}

/** A table row as its own screen's route returns it; each table kind reads its own fields. */
export type TableRow = Record<string, unknown>;

/** One panel's state. A `failed` panel carries no series, so it cannot be drawn as zeros (BR5). */
export type PanelResult =
  | {
      state: "ok";
      kind: string;
      read_at: string;
      bucket_seconds: number;
      data_starts_at: string | null;
      data_ends_at: string | null;
      /** The newest bucket of a live window, still filling: off the line, "so far" (FR66). */
      incomplete_from?: string | null;
      series: Series[];
      number?: NumberReading | null;
      rows?: TableRow[] | null;
      /** A metric series: how many series its one line combines (FR15, AC52). */
      series_combined?: number | null;
      /** Every unit a metric series' readings carried; more than one draws no line (AC51). */
      units?: string[] | null;
    }
  | { state: "empty"; kind: string; read_at: string; last_seen_at: string | null }
  | { state: "too_many_series"; kind: string; limit: number }
  | { state: "refused"; kind: string; retry_after_seconds: number }
  | { state: "window_too_wide"; kind: string; limit_days: number }
  | {
      state:
        | "not_in_project"
        | "needs_service"
        | "over_limit"
        | "timed_out"
        | "failed"
        | "ambiguous"
        | "series_changed"
        | "paused_by_operator";
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
const deletedKey = [...dashboardsKey, "deleted"] as const;
// Read by the screens that offer "Dashboards showing this service" (`shared/dashboards-for-service.ts`).
const forServiceKey = [...dashboardsKey, "for-service"] as const;

export function useDashboards() {
  return useQuery({
    queryKey: dashboardsKey,
    queryFn: ({ signal }) => apiClient.get<DashboardList>("dashboards", { signal }),
    select: (response) => response.dashboards,
  });
}

/** The deleted dashboards that can still be restored: Recently deleted (FR51). */
export function useDeletedDashboards() {
  return useQuery({
    queryKey: deletedKey,
    queryFn: ({ signal }) =>
      apiClient.get<DashboardList>("dashboards", { searchParams: { deleted: true }, signal }),
    select: (response) => response.dashboards,
  });
}

/** After any change to what exists: every list that could now be wrong is read again. */
function refreshLists(client: ReturnType<typeof useQueryClient>): Promise<unknown> {
  return Promise.all([
    client.invalidateQueries({ queryKey: dashboardsKey, exact: true }),
    client.invalidateQueries({ queryKey: deletedKey }),
    client.invalidateQueries({ queryKey: forServiceKey }),
  ]);
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

/** A file that passed every check a save makes, as the API read it (FR42, D53). */
export interface ValidatedDocument {
  document: DashboardDocument;
  panel_count: number;
  worst_case_reads: number;
}

/**
 * Import's check: the save's own checks with no read and no write. A refusal is said in
 * the import's own words, never a toast, since a 422 raises none (`query-client.ts`).
 */
export function useValidateDashboard() {
  return useMutation({
    mutationKey: ["dashboards", "validate"],
    mutationFn: (document: unknown) =>
      apiClient.post<ValidatedDocument>("dashboards/validate", document),
  });
}

/**
 * Where a checked file waits for the builder it opens in, keyed by a one-off id in the
 * address: nothing a file holds rides in the address, and a reload finds nothing and says
 * so rather than opening something unchecked.
 */
const importedKey = (id: string) => [...dashboardsKey, "imported", id] as const;

export function handOverImported(
  client: ReturnType<typeof useQueryClient>,
  document: DashboardDocument,
): string {
  const id = crypto.randomUUID();
  client.setQueryData(importedKey(id), document);
  return id;
}

/** The checked document a file opened, or undefined once it is gone. */
export function takeImported(
  client: ReturnType<typeof useQueryClient>,
  id: string,
): DashboardDocument | undefined {
  return client.getQueryData<DashboardDocument>(importedKey(id));
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
    onSuccess: () => refreshLists(client),
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
      client.removeQueries({ queryKey: [...dashboardsKey, dashboardId, "versions"] });
      return refreshLists(client);
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
      return refreshLists(client);
    },
  });
}

/**
 * Undo a delete (FR51). A title now in use is a 409 naming the holder; the caller asks for
 * another title and sends it as `title` (AC11).
 */
export function useRestoreDashboard(dashboardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["dashboards", "restore"],
    mutationFn: (title?: string) =>
      apiClient.post<Dashboard>(`dashboards/${dashboardId}/restore`, title ? { title } : {}),
    onSuccess: () => refreshLists(client),
  });
}

/**
 * Remove a dashboard, or one kept version of it, at once (FR68): an owner's or an admin's.
 * The dashboard's cached text goes too, so nothing on this page outlives it.
 */
export function usePurgeDashboard(dashboardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["dashboards", "purge"],
    mutationFn: (version?: number) =>
      apiClient.post<void>(`dashboards/${dashboardId}/purge`, version ? { version } : {}),
    onSuccess: (_nothing, version) => {
      if (version === undefined)
        client.removeQueries({ queryKey: [...dashboardsKey, dashboardId] });
      // A version's text may sit in a cached read of it; the history is read again.
      else client.removeQueries({ queryKey: [...versionsKey(dashboardId), version] });
      return Promise.all([
        refreshLists(client),
        version === undefined
          ? undefined
          : client.invalidateQueries({ queryKey: versionsKey(dashboardId), exact: true }),
      ]);
    },
  });
}

const versionsKey = (dashboardId: string) => [...dashboardsKey, dashboardId, "versions"] as const;

/** The history a dashboard keeps, newest first, with the one in use marked (FR51). */
export function useDashboardVersions(dashboardId: string) {
  return useQuery({
    queryKey: versionsKey(dashboardId),
    queryFn: ({ signal }) =>
      apiClient.get<{ versions: VersionSummary[] }>(`dashboards/${dashboardId}/versions`, {
        signal,
      }),
    select: (response) => response.versions,
  });
}

/** One kept version in full, for "Restore this version" to open as a draft. */
export function useDashboardVersion(dashboardId: string, version: number | undefined) {
  return useQuery({
    queryKey: [...versionsKey(dashboardId), version] as const,
    enabled: version !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<DashboardVersion>(`dashboards/${dashboardId}/versions/${version}`, { signal }),
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

/** A node as the infrastructure screen identifies it, with the metrics it sent. */
export interface NodeChoice {
  host: string;
  container: string;
  reporter: string;
  reporter_instance: string;
  signals: string[];
}

/**
 * The displayed project's nodes in the window, for a node chart's real values (FR23).
 * Read from the Infrastructure screen's own endpoint: features do not import one another.
 */
export function useNodeChoices(projectId: string | undefined, choice: WindowChoice) {
  return useQuery({
    queryKey: ["dashboard-node-choices", projectId, windowCacheKey(choice)] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) => {
      const window = resolveChoice(choice);
      return apiClient.get<{ nodes: NodeChoice[] }>("infrastructure/hosts", {
        searchParams: { project_id: projectId, from: window.from, to: window.to },
        signal,
      });
    },
    select: (response) => response.nodes,
  });
}

/** The displayed project's checks, by name, for a check chart's real values (FR23). */
export function useCheckChoices(projectId: string | undefined) {
  return useQuery({
    queryKey: ["dashboard-check-choices", projectId] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<{ checks: { name: string }[] }>("checks", {
        searchParams: { project_id: projectId },
        signal,
      }),
    // Names, once each: two checks of one name are one choice, which reads `ambiguous`.
    select: (response) => [...new Set(response.checks.map((check) => check.name))].sort(),
  });
}

/** A statement as the database screen keys it, for a statement chart's real values (FR23). */
export interface StatementChoice {
  query_hash: string;
  namespace: string;
  server: { address: string; port: number };
}

const STATEMENT_CHOICES_LIMIT = 100;

export function useStatementChoices(projectId: string | undefined, choice: WindowChoice) {
  return useQuery({
    queryKey: ["dashboard-statement-choices", projectId, windowCacheKey(choice)] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) => {
      const window = resolveChoice(choice);
      return apiClient.get<{ statements: StatementChoice[] }>("dbstats/statements", {
        searchParams: {
          project_id: projectId,
          from: window.from,
          to: window.to,
          limit: STATEMENT_CHOICES_LIMIT,
        },
        signal,
      });
    },
    select: (response) => response.statements,
  });
}

/** The two shapes a metric series panel draws as a line (FR6). */
export type MetricShape = "gauge" | "sum";

/** A metric's names in one shape, busiest first, through the catalogue (T8.4). */
export function useMetricChoices(
  projectId: string | undefined,
  shape: MetricShape | undefined,
  choice: WindowChoice,
) {
  return useQuery({
    queryKey: ["dashboard-metric-choices", projectId, shape, windowCacheKey(choice)] as const,
    enabled: projectId !== undefined && shape !== undefined,
    queryFn: ({ signal }) => {
      const window = resolveChoice(choice);
      return apiClient.get<{ metrics: { metric_name: string }[] }>("metrics", {
        searchParams: { project_id: projectId, from: window.from, to: window.to, shape },
        signal,
      });
    },
    select: (response) => response.metrics.map((metric) => metric.metric_name),
  });
}

/** One attribute combination of a metric, which a panel may choose by its hash (AC83). */
export interface MetricSeriesChoice {
  /** Decimal digits: the hash is 64 bits, more than a number here holds. */
  series_hash: string;
  attributes: MetricAttribute[];
  points: number;
  last_seen: string;
}

/** The series list reads at most this many days (AC121); a wider window lists its last 7. */
export const SERIES_LIST_MAX_DAYS = 7;
const MS_PER_DAY = 86_400_000;

/** The window the series list may read: the builder's, or its last 7 days. */
export function seriesListWindow(window: { from: string; to: string }): {
  from: string;
  to: string;
} {
  const earliest = Date.parse(window.to) - SERIES_LIST_MAX_DAYS * MS_PER_DAY;
  return Date.parse(window.from) < earliest
    ? { from: new Date(earliest).toISOString(), to: window.to }
    : window;
}

export function useMetricSeriesChoices(
  ask: { projectId: string; metric: string; service: string; shape: MetricShape } | undefined,
  choice: WindowChoice,
) {
  return useQuery({
    queryKey: ["dashboard-metric-series", ask, windowCacheKey(choice)] as const,
    enabled: ask !== undefined,
    queryFn: ({ signal }) => {
      const window = seriesListWindow(resolveChoice(choice));
      return apiClient.get<{ series: MetricSeriesChoice[]; truncated: boolean }>(
        `metrics/${encodeURIComponent(ask?.metric ?? "")}/series`,
        {
          searchParams: {
            project_id: ask?.projectId,
            service_name: ask?.service,
            shape: ask?.shape,
            from: window.from,
            to: window.to,
          },
          signal,
        },
      );
    },
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

/**
 * What this page refreshes at: the dashboard's setting, or this tab's own choice over it
 * (FR71), within the floor. A fixed window never refreshes, and a paused page does not:
 * both beat any choice a tab made (design, *The refresh control's states*).
 */
export function effectiveIntervalMs(parts: {
  saved: number | false;
  tab: number | undefined;
  fixedWindow: boolean;
  paused: boolean;
}): number | false {
  if (parts.fixedWindow || parts.paused) return false;
  if (parts.tab !== undefined) return Math.max(parts.tab, DASHBOARD_REFRESH_FLOOR_MS);
  return parts.saved;
}

/** What one page reads: a saved dashboard against a project, a window and a picked service. */
export interface PanelReadScope {
  dashboardId: string;
  projectId: string;
  choice: WindowChoice;
  service?: string;
}

/** The batch's request, for the whole page and for a catch-up that reads some panels. */
export function readPanels(
  scope: PanelReadScope,
  panelIds: readonly string[] | undefined,
  signal?: AbortSignal,
): Promise<PanelReadResponse> {
  return apiClient.get<PanelReadResponse>("dashboard-panels/read", {
    searchParams: {
      dashboard_id: scope.dashboardId,
      project_id: scope.projectId,
      ...windowParams(scope.choice),
      ...(scope.service === undefined ? {} : { service: scope.service }),
      ...(panelIds === undefined ? {} : { panels: panelIds.join(",") }),
    },
    timeoutMs: PANEL_READ_CLIENT_TIMEOUT_MS,
    signal,
  });
}

/** Whole-request refusals in a row, counted once each however often the interval is asked. */
export interface RefusalStreak {
  errorAt: number;
  dataAt: number;
  refusals: number;
}

export function newRefusalStreak(): RefusalStreak {
  return { errorAt: 0, dataAt: 0, refusals: 0 };
}

/** How a page reads: the interval it refreshes at, and whether it may read yet. */
export interface ReadOptions {
  intervalMs: number | false;
  /** The address and the selection agree (AC27): until then, no read leaves the page. */
  enabled: boolean;
}

export function useDashboardContext(
  dashboard: Dashboard | undefined,
  projectId: string | undefined,
  choice: WindowChoice,
  options: ReadOptions = { intervalMs: false, enabled: true },
) {
  return useQuery({
    queryKey: ["dashboard-context", dashboard?.id, projectId, windowCacheKey(choice)] as const,
    enabled: options.enabled && dashboard !== undefined && projectId !== undefined,
    refetchInterval: options.intervalMs,
    queryFn: ({ signal }) =>
      apiClient.get<DashboardContext>(`dashboards/${dashboard?.id}/context`, {
        searchParams: { project_id: projectId, ...windowParams(choice) },
        signal,
      }),
  });
}

/** A whole-request 429 is tried again at most this many times, then waits for "Read again" (FR45). */
export const WHOLE_REQUEST_RETRIES = 3;

/**
 * When the page reads next. The page's interval, except after a whole-request 429: then
 * not sooner than the server's `Retry-After` says, even on a fixed window or with refresh
 * off, since that read never finished (AC145). Paused is false whatever happened.
 */
export function nextReadAfterMs(parts: {
  intervalMs: number | false;
  paused: boolean;
  error: unknown;
  /** How many whole-request refusals in a row, this one included. */
  refusals: number;
}): number | false {
  if (parts.paused) return false;
  if (isRateLimited(parts.error) && parts.refusals <= WHOLE_REQUEST_RETRIES) {
    const wait = (parts.error as ApiError).context.retryAfterSeconds ?? DEFAULT_RETRY_AFTER_SECONDS;
    const asked = Math.max(wait, 1) * MS_PER_SECOND;
    return parts.intervalMs === false ? asked : Math.max(asked, parts.intervalMs);
  }
  return parts.intervalMs;
}

const DEFAULT_RETRY_AFTER_SECONDS = 5;

export function useDashboardPanels(
  dashboard: Dashboard | undefined,
  projectId: string | undefined,
  choice: WindowChoice,
  /** After a drag, only these panels are read; the rest as they scroll in (FR53). */
  panelIds: readonly string[] | undefined,
  options: ReadOptions & { service?: string; paused: boolean; streak: { current: RefusalStreak } },
) {
  const { intervalMs, service, streak } = options;
  // The choice and not the instants: the key stays still while a live window moves, so
  // a refresh never brings the skeleton back (AC29).
  const read = [
    "dashboard-panels",
    dashboard?.id,
    dashboard?.version,
    projectId,
    windowCacheKey(choice),
    service,
  ] as const;
  return useQuery<PanelReadResponse>({
    queryKey: [...read, panelIds?.join(",")] as const,
    enabled:
      options.enabled &&
      dashboard !== undefined &&
      projectId !== undefined &&
      panelIds?.length !== 0,
    refetchInterval: (query) => {
      const { error, errorUpdatedAt, dataUpdatedAt } = query.state;
      const seen = streak.current;
      if (dataUpdatedAt !== seen.dataAt) {
        seen.dataAt = dataUpdatedAt;
        seen.refusals = 0;
      }
      if (errorUpdatedAt !== seen.errorAt) {
        seen.errorAt = errorUpdatedAt;
        seen.refusals = isRateLimited(error) ? seen.refusals + 1 : 0;
      }
      return nextReadAfterMs({
        intervalMs,
        paused: options.paused,
        error,
        refusals: seen.refusals,
      });
    },
    // Growing the panels read keeps what is drawn: the panels already read come from the
    // server's kept answers at no cost (BR6). Only within one read: a change of project or
    // window never carries an old reading across (FR31).
    placeholderData: (
      previous: PanelReadResponse | undefined,
      previousQuery?: { queryKey: readonly unknown[] },
    ) =>
      read.every((part, index) => previousQuery?.queryKey[index] === part) ? previous : undefined,
    // Back on the tab, it reads again only once past its own interval.
    staleTime: intervalMs === false ? Number.POSITIVE_INFINITY : intervalMs,
    // Each refresh is the retry: a cold ClickHouse's 503 must not be read three times a
    // minute on a wall display (AC61).
    retry: false,
    // Said on the page in its own words, so the console raises no toast (AC61, AC145).
    meta: { ownWords: ["unavailable", "rate-limited"] },
    queryFn: ({ signal }) => {
      if (dashboard === undefined || projectId === undefined) {
        throw new Error("a read needs a dashboard and a project");
      }
      return readPanels(
        { dashboardId: dashboard.id, projectId, choice, service },
        panelIds,
        signal,
      );
    },
  });
}
