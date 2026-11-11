import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/** The three plain-HTTP formats (F3 FD3). The screen offers only those with a real call. */
export type WireFormat = "openai_chat" | "anthropic_messages" | "bedrock_converse";
export type Missing = "connection" | "test" | "limit" | "consent" | "container";
export type UnavailableReason = "hosted" | "account_missing" | "no_key";
export type CheckReason =
  | "no_connection"
  | "not_allowed"
  | "key_unreadable"
  | "address_refused"
  | "timed_out"
  | "unreachable"
  | "bad_key"
  | "not_found"
  | "bad_request"
  | "limited"
  | "unavailable"
  | "refused"
  | "too_long"
  | "invalid";

export interface Consent {
  by_user_id: string | null;
  by_name: string | null;
  by_left: boolean;
  at: string;
  current: boolean;
}

export interface ConnectionView {
  wire_format: WireFormat;
  base_url: string;
  provider_host: string;
  address_kind: "private" | "public";
  model: string;
  region_label: string | null;
  has_key: boolean;
  call_seconds: number;
  monthly_token_limit: number | null;
  price_input: string | null;
  price_output: string | null;
  checked_at: string | null;
  checked_ok: boolean | null;
  test_current: boolean;
  model_changed: boolean;
  consent: Consent | null;
}

/** `GET /ai/connection`. Without `manage_ai`, `full` is false and `on` is all there is. */
export interface AISettings {
  on: boolean;
  full: boolean;
  available?: boolean | null;
  unavailable_reason?: UnavailableReason | null;
  container_running?: boolean | null;
  beat_lock_exposed?: boolean | null;
  off_reason?: "person" | "consent_ended" | null;
  missing?: Missing[];
  connection?: ConnectionView | null;
  formats_offered?: WireFormat[];
  consent_fingerprint?: string | null;
  schema_version?: number | null;
  suggested_monthly_tokens?: number;
}

export interface SaveConnection {
  wire_format: WireFormat;
  base_url: string;
  model: string;
  region_label: string | null;
  /** Absent keeps the saved key, unless the address or format changes. */
  key?: string;
  call_seconds: number;
  monthly_token_limit: number | null;
  price_input: string | null;
  price_output: string | null;
}

export interface CheckResult {
  pending: boolean;
  ok?: boolean | null;
  reason?: CheckReason | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

export interface Preview {
  sample: boolean;
  finding_id: string | null;
  read_at: string | null;
  summary: string[];
  method: string;
  url: string;
  headers: { name: string; value: string }[];
  instructions: string;
  body: string;
  request_sha256: string;
  prompt_version: string;
  aliases: { alias: string; stands_for: string }[];
  sendable_fields: string[];
  fact_kinds: string[];
}

export interface ProjectOptOut {
  project_id: string;
  name: string;
  opted_out: boolean;
}

export interface Usage {
  month: string;
  resets_on: string;
  used_tokens: number;
  reserved_tokens: number;
  monthly_token_limit: number | null;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: string | null;
  worst_case_cost: string | null;
}

export interface RunRow {
  id: string;
  started_at: string;
  finished_at: string | null;
  seconds: number | null;
  purpose: string;
  outcome: string;
  finding_id: string | null;
  provider_host: string;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  tokens_estimated: boolean;
  fields_sent: string[];
  by_person: boolean;
  host_consent: "consented" | "not_consented" | "not_applicable";
}

export interface SentView {
  match: "matches" | "cannot_reproduce" | "expired";
  statement: string;
  instructions: string | null;
  body: string | null;
  facts: string | null;
}

const ROOT = ["ai"] as const;
const SETTINGS_KEY = [...ROOT, "connection"] as const;
// How often a waiting test is asked again, and how long the screen waits before saying the
// AI container did not answer (spec, *Error cases*: 60 seconds, a guess).
export const CHECK_POLL_MS = 2000;
export const CHECK_ANSWER_MS = 60_000;

export function useAISettings() {
  return useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }) => apiClient.get<AISettings>("ai/connection", { signal }),
  });
}

function useInvalidating<Input, Output>(run: (input: Input) => Promise<Output>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ROOT }),
  });
}

export function useSaveConnection() {
  return useInvalidating((body: SaveConnection) =>
    apiClient.put<AISettings>("ai/connection", body),
  );
}

export function useDeleteConnection() {
  return useInvalidating(() => apiClient.delete<void>("ai/connection"));
}

export function useStartCheck() {
  return useMutation({
    mutationFn: () => apiClient.post<{ request_id: string }>("ai/connection/check"),
  });
}

/** A test's result, asked for every two seconds while it is pending. */
export function useCheckResult(requestId: string | null) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: [...ROOT, "check", requestId],
    enabled: requestId !== null,
    queryFn: async ({ signal }) => {
      const result = await apiClient.get<CheckResult>(`ai/connection/check/${requestId}`, {
        signal,
      });
      if (!result.pending) void queryClient.invalidateQueries({ queryKey: SETTINGS_KEY });
      return result;
    },
    refetchInterval: (query) => (query.state.data?.pending === false ? false : CHECK_POLL_MS),
  });
}

export function usePreview(enabled: boolean) {
  return useQuery({
    queryKey: [...ROOT, "preview"],
    enabled,
    queryFn: ({ signal }) => apiClient.get<Preview>("ai/preview", { signal }),
  });
}

export function useConsent() {
  return useInvalidating((body: { fingerprint: string; schema_version: number }) =>
    apiClient.post<AISettings>("ai/consent", body),
  );
}

export function useSwitch() {
  return useInvalidating((on: boolean) => apiClient.put<{ on: boolean }>("ai/switch", { on }));
}

export function useOptOuts(enabled: boolean) {
  return useQuery({
    queryKey: [...ROOT, "project-opt-outs"],
    enabled,
    queryFn: ({ signal }) => apiClient.get<ProjectOptOut[]>("ai/project-opt-outs", { signal }),
  });
}

export function useSetOptOut() {
  return useInvalidating(({ projectId, optedOut }: { projectId: string; optedOut: boolean }) =>
    optedOut
      ? apiClient.put<ProjectOptOut>(`ai/project-opt-outs/${projectId}`)
      : apiClient.delete<ProjectOptOut>(`ai/project-opt-outs/${projectId}`),
  );
}

export function useUsage(enabled: boolean) {
  return useQuery({
    queryKey: [...ROOT, "usage"],
    enabled,
    queryFn: ({ signal }) => apiClient.get<Usage>("ai/usage", { signal }),
  });
}

export function useRuns(enabled: boolean) {
  return useQuery({
    queryKey: [...ROOT, "runs"],
    enabled,
    queryFn: ({ signal }) => apiClient.get<RunRow[]>("ai/runs", { signal }),
  });
}

export function useSent(runId: string | null) {
  return useQuery({
    queryKey: [...ROOT, "sent", runId],
    enabled: runId !== null,
    queryFn: ({ signal }) => apiClient.get<SentView>(`ai/runs/${runId}/sent`, { signal }),
  });
}
