import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * Alert rules, incidents and notification channels (F21). Field names are the API's.
 *
 * A channel's secret goes one way. It is sent when a channel is created or its
 * secret replaced, and no read ever returns it; a webhook's signing secret comes
 * back once, on the create or rotate that generated it, and is held in component
 * state by the screen that shows it — never in this cache.
 */

/* Open incidents are what someone opens this screen for. */
const INCIDENT_REFRESH_MS = 15_000;
/* A send test waits this long server-side for the worker's answer. */
const SEND_TEST_WAIT_SECONDS = 10;

export type Severity = "critical" | "warning";
export type AlertableState = "down" | "degraded" | "misconfigured";

export interface CheckStateCondition {
  kind: "check_state";
  /** Null covers every check in the project, including ones created later. */
  check_ids: string[] | null;
  states: Partial<Record<AlertableState, Severity>>;
}

interface ServiceScope {
  /** Null covers every service in the project, including ones that start later. */
  services: string[] | null;
  severity: Severity;
}

interface Threshold extends ServiceScope {
  window_minutes: number;
  min_requests: number;
  fire_after_minutes: number;
  resolve_after_minutes: number;
}

export interface ErrorRateCondition extends Threshold {
  kind: "service_error_rate";
  threshold_percent: number;
}

export interface LatencyCondition extends Threshold {
  kind: "service_latency_p95";
  threshold_ms: number;
}

export interface SilenceCondition extends ServiceScope {
  kind: "service_silent";
  silent_minutes: number;
}

export interface NewErrorCondition extends ServiceScope {
  kind: "new_error_group";
}

export type Condition =
  | CheckStateCondition
  | ErrorRateCondition
  | LatencyCondition
  | SilenceCondition
  | NewErrorCondition;
export type ConditionKind = Condition["kind"];

export interface RuleChannel {
  id: string;
  kind: ChannelKind;
  name: string;
}

export interface AlertRule {
  id: string;
  project_id: string;
  name: string;
  condition_kind: Condition["kind"];
  condition: Condition;
  channels: RuleChannel[];
  renotify_minutes: number | null;
  enabled: boolean;
  muted_until: string | null;
  muted_by: string | null;
  is_default: boolean;
  notifying_nobody: boolean;
  covers_nothing: boolean;
  created_at: string;
  updated_at: string;
}

export interface WriteRule {
  project_id: string;
  name: string;
  condition: Condition;
  channel_ids: string[];
  renotify_minutes: number | null;
  enabled: boolean;
}

export interface IncidentNotification {
  id: string;
  channel_id: string;
  channel_name: string;
  channel_kind: ChannelKind;
  kind: "opened" | "escalated" | "resolved" | "repeated" | "flapping" | "closed";
  state: "pending" | "delivering" | "delivered" | "failed" | "dropped";
  reason: string | null;
  delivered_at: string | null;
}

export interface Incident {
  id: string;
  project_id: string;
  rule_id: string | null;
  rule_name: string;
  condition_kind: string;
  subject_key: string;
  subject_label: string;
  severity: Severity;
  opened_at: string;
  resolved_at: string | null;
  is_event: boolean;
  flapping: boolean;
  muted: boolean;
  // The check it was about has since been deleted (its page would say "No such check").
  check_deleted: boolean;
  detail: {
    check_id?: string;
    state?: string;
    reason?: string | null;
    location?: string;
    service?: string;
    reading?: string;
    value?: number | null;
    threshold?: number;
    unit?: string;
    exception_type?: string;
  };
  notifications: IncidentNotification[];
}

export interface IncidentList {
  open: Incident[];
  recent: Incident[];
}

export type ChannelKind =
  | "owners"
  | "email"
  | "slack"
  | "teams"
  | "discord"
  | "pagerduty"
  | "webhook";

export interface ChannelSummary {
  id: string;
  kind: ChannelKind;
  name: string;
  config: { addresses?: string[]; url?: string; region?: "us" | "eu"; host?: string };
  /** A host, never a path: a Slack or Discord URL's path is its credential. */
  destination: string | null;
  secret_set: boolean;
  health: "healthy" | "failing";
  failing_since: string | null;
  last_delivered_at: string | null;
  muted_until: string | null;
  muted_by: string | null;
  quiet_hours: QuietHours | null;
  created_at: string;
  rules: { id: string; name: string; project_id: string }[];
}

/** Monday is 0. An end before the start runs past midnight. */
export interface QuietHours {
  timezone: string;
  days: number[];
  start: string;
  end: string;
}

export interface CreatedChannel extends ChannelSummary {
  /** A webhook's signing secret. Shown once; never cached. */
  signing_secret: string | null;
}

export interface CreateChannel {
  kind: Exclude<ChannelKind, "owners">;
  name: string;
  config: Record<string, unknown>;
  secret?: string;
}

export interface UpdateChannel {
  name?: string;
  config?: Record<string, unknown>;
  secret?: string;
}

export interface DeliveryAttempt {
  id: string;
  attempted_at: string;
  outcome: "delivered" | "retrying" | "failed";
  http_status: number | null;
  reason: string | null;
  notifications: string[];
}

export interface SendTestResult {
  pending: boolean;
  delivered: boolean;
  http_status: number | null;
  reason: string | null;
}

export interface OpenIncidentCount {
  open: number;
  critical: number;
}

export interface Coverage {
  rules: AlertRule[];
  open_incidents: Incident[];
}

/* ------------------------------------------------------------------ keys */

export const rulesQueryKey = (projectId: string) => ["alert-rules", projectId] as const;
export const ruleQueryKey = (ruleId: string) => ["alert-rule", ruleId] as const;
export const incidentsQueryKey = (projectId: string) => ["incidents", projectId] as const;
export const incidentQueryKey = (incidentId: string) => ["incidents", "one", incidentId] as const;
// Under "incidents", so every change that already refreshes the list refreshes the count.
export const openIncidentCountQueryKey = (projectId: string) =>
  ["incidents", "open-count", projectId] as const;
export const channelsQueryKey = ["channels"] as const;
export const channelQueryKey = (channelId: string) => ["channel", channelId] as const;
export const deliveryLogQueryKey = (channelId: string) =>
  ["channel-deliveries", channelId] as const;

/* ----------------------------------------------------------------- rules */

export function useAlertRules(projectId: string | undefined) {
  return useQuery({
    queryKey: rulesQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<{ rules: AlertRule[] }>("alerts/rules", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.rules,
  });
}

export function useAlertRule(ruleId: string | undefined) {
  return useQuery({
    queryKey: ruleQueryKey(ruleId ?? ""),
    enabled: ruleId !== undefined,
    queryFn: ({ signal }) => apiClient.get<AlertRule>(`alerts/rules/${ruleId}`, { signal }),
  });
}

function useInvalidateAlerts() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["alert-rules"] }),
      client.invalidateQueries({ queryKey: ["alert-rule"] }),
      client.invalidateQueries({ queryKey: ["alert-coverage"] }),
      // Deleting, disabling or narrowing a rule closes incidents.
      client.invalidateQueries({ queryKey: ["incidents"] }),
      client.invalidateQueries({ queryKey: channelsQueryKey }),
      client.invalidateQueries({ queryKey: ["channel"] }),
    ]);
}

export function useSaveRule() {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: ({ ruleId, rule }: { ruleId?: string; rule: WriteRule }) =>
      ruleId === undefined
        ? apiClient.post<AlertRule>("alerts/rules", rule)
        : apiClient.patch<AlertRule>(`alerts/rules/${ruleId}`, {
            name: rule.name,
            condition: rule.condition,
            channel_ids: rule.channel_ids,
            enabled: rule.enabled,
            ...(rule.renotify_minutes === null
              ? { clear_renotify: true }
              : { renotify_minutes: rule.renotify_minutes }),
          }),
    onSuccess: invalidate,
  });
}

export function useDeleteRule() {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: (ruleId: string) => apiClient.delete<void>(`alerts/rules/${ruleId}`),
    onSuccess: invalidate,
  });
}

export function useMuteRule() {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: ({ ruleId, until }: { ruleId: string; until: string | null }) =>
      apiClient.put<AlertRule>(`alerts/rules/${ruleId}/mute`, { until }),
    onSuccess: invalidate,
  });
}

/* ------------------------------------------------------------- incidents */

export function useIncidents(projectId: string | undefined) {
  return useQuery({
    queryKey: incidentsQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    refetchInterval: INCIDENT_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<IncidentList>("alerts/incidents", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}

/** One incident: what every alert message links to (FR16). */
export function useIncident(incidentId: string) {
  return useQuery({
    queryKey: incidentQueryKey(incidentId),
    refetchInterval: INCIDENT_REFRESH_MS,
    queryFn: ({ signal }) => apiClient.get<Incident>(`alerts/incidents/${incidentId}`, { signal }),
  });
}

/** The sidebar's count: asked on every screen, so a count rather than the list. */
export function useOpenIncidentCount(projectId: string | undefined) {
  return useQuery({
    queryKey: openIncidentCountQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    refetchInterval: INCIDENT_REFRESH_MS,
    queryFn: ({ signal }) =>
      apiClient.get<OpenIncidentCount>("alerts/incidents/open-count", {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}

/* -------------------------------------------------------------- channels */

export function useChannels() {
  return useQuery({
    queryKey: channelsQueryKey,
    queryFn: ({ signal }) => apiClient.get<{ channels: ChannelSummary[] }>("channels", { signal }),
    select: (response) => response.channels,
  });
}

export function useChannel(channelId: string) {
  return useQuery({
    queryKey: channelQueryKey(channelId),
    queryFn: ({ signal }) => apiClient.get<ChannelSummary>(`channels/${channelId}`, { signal }),
  });
}

export function useCreateChannel() {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: (channel: CreateChannel) => apiClient.post<CreatedChannel>("channels", channel),
    onSuccess: invalidate,
  });
}

export function useUpdateChannel(channelId: string) {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: (update: UpdateChannel) =>
      apiClient.patch<ChannelSummary>(`channels/${channelId}`, update),
    onSuccess: invalidate,
  });
}

export function useMuteChannel(channelId: string) {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: (until: string | null) =>
      apiClient.put<ChannelSummary>(`channels/${channelId}/mute`, { until }),
    onSuccess: invalidate,
  });
}

export function useSetQuietHours(channelId: string) {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: (quietHours: QuietHours | null) =>
      apiClient.put<ChannelSummary>(`channels/${channelId}/quiet-hours`, {
        quiet_hours: quietHours,
      }),
    onSuccess: invalidate,
  });
}

export function useDeleteChannel() {
  const invalidate = useInvalidateAlerts();
  return useMutation({
    mutationFn: (channelId: string) => apiClient.delete<void>(`channels/${channelId}`),
    onSuccess: invalidate,
  });
}

export function useRotateSigningSecret(channelId: string) {
  return useMutation({
    mutationFn: () =>
      apiClient.post<{ signing_secret: string }>(`channels/${channelId}/signing-secret`),
  });
}

export function useDeliveryLog(channelId: string) {
  return useQuery({
    queryKey: deliveryLogQueryKey(channelId),
    queryFn: ({ signal }) =>
      apiClient.get<{ attempts: DeliveryAttempt[] }>(`channels/${channelId}/deliveries`, {
        signal,
      }),
    select: (response) => response.attempts,
  });
}

/**
 * Send a test and wait for its outcome: dispatch, then one read that waits up to ten
 * seconds server-side. `pending` in the answer means the worker has not replied yet,
 * which the screen says rather than guessing.
 */
export function useSendTest() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (channelId: string) => {
      const accepted = await apiClient.post<{ request_id: string }>("channel-tests", {
        channel_id: channelId,
      });
      return apiClient.get<SendTestResult>(`channel-tests/${accepted.request_id}`, {
        searchParams: { wait_seconds: String(SEND_TEST_WAIT_SECONDS) },
      });
    },
    onSuccess: (_, channelId) =>
      Promise.all([
        client.invalidateQueries({ queryKey: channelQueryKey(channelId) }),
        client.invalidateQueries({ queryKey: channelsQueryKey }),
      ]),
  });
}

/* -------------------------------------------------------------- evaluation */

export interface EvaluationStatus {
  sweeps: {
    name: "thresholds" | "silence" | "new_errors";
    finished_at: string | null;
    skipped: string | null;
    behind: boolean;
  }[];
}

/** Whether the service conditions are being judged now (spec, *Error cases*). */
export function useEvaluationStatus() {
  return useQuery({
    queryKey: ["alert-evaluation"],
    refetchInterval: INCIDENT_REFRESH_MS * 4,
    queryFn: ({ signal }) => apiClient.get<EvaluationStatus>("alerts/evaluation", { signal }),
  });
}

/* -------------------------------------------------------------- services */

export interface ServiceReading {
  service_name: string;
  request_count: number;
  error_rate: number;
  p95_ms: number;
}

const HOUR_MS = 3_600_000;

/**
 * The project's services over the last hour — who can be chosen, and the reading a
 * threshold is being set against (design, *Rule editor*). Read from the Services
 * screen's own endpoint, so the number is the one that screen shows.
 */
export function useServiceReadings(projectId: string | undefined) {
  return useQuery({
    queryKey: ["alert-service-readings", projectId ?? ""],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) => {
      const to = new Date();
      return apiClient.get<{ services: ServiceReading[] }>("services", {
        searchParams: {
          project_id: projectId,
          from: new Date(to.getTime() - HOUR_MS).toISOString(),
          to: to.toISOString(),
        },
        signal,
      });
    },
    select: (response) => response.services,
  });
}

/* ---------------------------------------------------------------- checks */

/** The project's checks, by name, for a rule's scope. Read from the checks endpoint
 *  directly: features do not import one another. */
export function useCheckChoices(projectId: string | undefined) {
  return useQuery({
    queryKey: ["alert-check-choices", projectId ?? ""],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<{ checks: { id: string; name: string }[] }>("checks", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.checks.map(({ id, name }) => ({ id, name })),
  });
}
