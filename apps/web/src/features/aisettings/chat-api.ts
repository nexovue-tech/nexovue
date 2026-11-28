import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/** Chat's half of `/settings/ai` (F4 T2.12), on `/assistant/settings`, `/assistant/reads`
 *  and `/ai/preview?purpose=chat`. Admins only, as the screen is. */

export type DataLevel = "facts" | "facts_and_text";

export interface ChatSettings {
  enabled: boolean;
  daily_question_limit: number;
  data_level: DataLevel;
  mask_emails: boolean;
  consented_at: string | null;
  consented_by_name: string | null;
  consent_current: boolean;
  consent_fingerprint: string | null;
  disclosure_version: number;
  monthly_counts: Record<string, number>;
  people_this_month: { name: string; questions: number }[];
  embedding: EmbeddingState;
}

/** Why chat searches by keyword only (FR27, AC43); null when it searches by meaning too. */
export type KeywordOnlyReason =
  | "no_extension"
  | "no_model"
  | "cannot_embed"
  | "no_consent"
  | "not_tested"
  | "chat_not_ready";

/** Meaning search's state (F4 Step 6). Never a key, never an address's path. */
export interface EmbeddingState {
  model: string | null;
  source: "chat" | "own" | null;
  own_base_url: string | null;
  own_host: string | null;
  own_wire_format: string | null;
  own_has_key: boolean;
  own_consent_fingerprint: string | null;
  own_consent_current: boolean;
  check: "passed" | "failed" | "not_tested";
  check_reason: string | null;
  checked_at: string | null;
  vector_available: boolean;
  keyword_only: KeywordOnlyReason | null;
  chunks_embedded: number;
  chunks_total: number;
  /** Notes chat cannot search yet, or at all at a project's cap (FR32, AC83). */
  notes_not_searchable: number;
}

export interface SaveEmbedding {
  model: string | null;
  own: { wire_format: string; base_url: string; key?: string } | null;
}

export interface ChatPreview {
  provider_label: string;
  address: string;
  wire_format: string;
  model: string;
  project_id: string;
  project_name: string;
  kinds: { kind: string; source: string; level: DataLevel }[];
  samples: { kind: string; labelled_sample: boolean; sent: string }[];
  masking_rules_off: string[];
  mask_emails: boolean;
  sent_for_embedding: number;
  consent_fingerprint: string;
  disclosure_version: number;
}

export interface ReadRow {
  at: string;
  person: string | null;
  project: string | null;
  tool: string;
  from_time: string | null;
  to_time: string | null;
  row_count: number | null;
  provider_label: string;
}

export interface ChatAvailability {
  tested: boolean;
  waiting_for: string[];
}

const ROOT = ["ai", "chat"] as const;
const CHECK_POLL_MS = 2000;

/** `poll` reads it again every 2 s, while an embedding check's result is awaited. */
export function useChatSettings({ poll = false }: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: [...ROOT, "settings"],
    refetchInterval: poll ? CHECK_POLL_MS : false,
    queryFn: ({ signal }) => apiClient.get<ChatSettings>("assistant/settings", { signal }),
  });
}

export function useChatPreview(projectId: string | undefined) {
  return useQuery({
    queryKey: [...ROOT, "preview", projectId],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ChatPreview>("ai/preview", {
        signal,
        searchParams: { purpose: "chat", project_id: projectId },
      }),
  });
}

export function useChatReads() {
  return useQuery({
    queryKey: [...ROOT, "reads"],
    queryFn: ({ signal }) => apiClient.get<ReadRow[]>("assistant/reads", { signal }),
  });
}

/** Whether the model was tested, and whose consent chat waits on, for the selected project. */
export function useChatAvailability(projectId: string | undefined) {
  return useQuery({
    queryKey: [...ROOT, "availability", projectId],
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ChatAvailability>("assistant/availability", {
        signal,
        searchParams: { project_id: projectId },
      }),
  });
}

function useChatMutation<Input>(run: (input: Input) => Promise<ChatSettings>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ROOT }),
  });
}

export function useSaveChatSettings() {
  return useChatMutation(
    (body: {
      enabled: boolean;
      daily_question_limit: number;
      data_level: DataLevel;
      mask_emails: boolean;
    }) => apiClient.put<ChatSettings>("assistant/settings", body),
  );
}

export function useSaveEmbedding() {
  return useChatMutation((body: SaveEmbedding) =>
    apiClient.put<ChatSettings>("assistant/settings/embedding", body),
  );
}

export function useEmbeddingConsent() {
  return useChatMutation((body: { fingerprint: string }) =>
    apiClient.post<ChatSettings>("assistant/settings/embedding/consent", body),
  );
}

/** One real call on the AI container; the result lands on the settings, read again. */
export function useCheckEmbedding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<null>("assistant/settings/embedding/check", {}),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ROOT }),
  });
}

export function useChatConsent() {
  return useChatMutation(
    (body: { fingerprint: string; disclosure_version: number; data_level: DataLevel }) =>
      apiClient.post<ChatSettings>("assistant/settings/consent", body),
  );
}
