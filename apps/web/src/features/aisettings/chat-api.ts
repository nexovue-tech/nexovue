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

export function useChatSettings() {
  return useQuery({
    queryKey: [...ROOT, "settings"],
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

export function useChatConsent() {
  return useChatMutation(
    (body: { fingerprint: string; disclosure_version: number; data_level: DataLevel }) =>
      apiClient.post<ChatSettings>("assistant/settings/consent", body),
  );
}
