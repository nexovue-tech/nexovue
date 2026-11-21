import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/** `nexovue_api/runbooks/schemas.py`, as the screen reads it. */
export interface RunbookSummary {
  id: string;
  project_id: string;
  title: string;
  service_name: string | null;
  /** A current member's name or email; null reads "Former member". */
  author: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunbookView extends RunbookSummary {
  /** Null for a support view, which sees no bodies (FR32). */
  content: string | null;
}

export interface RunbookList {
  runbooks: RunbookSummary[];
  limit: number;
}

export interface RunbookFields {
  title: string;
  service_name: string | null;
  content: string;
  confirm_credentials: boolean;
}

export interface UploadedFile {
  name: string;
  content: string;
}

export interface RefusedFile {
  name: string;
  project_id: string;
  code: string;
  detail: string;
}

export interface UploadResult {
  created: RunbookSummary[];
  refused: RefusedFile[];
}

/** What a writer learns of chat before pasting: who the text may go to (design). */
export interface ChatStanding {
  state: string;
  provider_label: string | null;
}

const ROOT = ["runbooks"] as const;
const listKey = (projectId: string) => [...ROOT, "list", projectId] as const;
const oneKey = (runbookId: string) => [...ROOT, "one", runbookId] as const;

/** `GET /runbooks?project_id=`: every signed-in person reads the list. */
export function useRunbooks(projectId: string | undefined) {
  return useQuery({
    queryKey: listKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<RunbookList>(`runbooks?project_id=${encodeURIComponent(projectId ?? "")}`, {
        signal,
      }),
  });
}

/** `GET /runbooks/{id}`: one runbook; a support view gets no body. */
export function useRunbook(runbookId: string) {
  return useQuery({
    queryKey: oneKey(runbookId),
    queryFn: ({ signal }) =>
      apiClient.get<RunbookView>(`runbooks/${encodeURIComponent(runbookId)}`, { signal }),
  });
}

/**
 * `GET /assistant/availability`: whether chat is ready here and its provider's safe label,
 * for the form's "may be sent to" sentence. Only asked by someone who may ask chat.
 */
export function useChatStanding(projectId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["runbooks", "chat-standing", projectId ?? ""],
    enabled: enabled && projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ChatStanding>(
        `assistant/availability?project_id=${encodeURIComponent(projectId ?? "")}`,
        { signal },
      ),
  });
}

function useInvalidateRunbooks() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ROOT });
}

export function useCreateRunbook(projectId: string) {
  const invalidate = useInvalidateRunbooks();
  return useMutation({
    mutationFn: (fields: RunbookFields) =>
      apiClient.post<RunbookView>("runbooks", { ...fields, project_id: projectId }),
    onSuccess: invalidate,
  });
}

export function useUpdateRunbook(runbookId: string) {
  const invalidate = useInvalidateRunbooks();
  return useMutation({
    mutationFn: (fields: RunbookFields) =>
      apiClient.put<RunbookView>(`runbooks/${encodeURIComponent(runbookId)}`, fields),
    onSuccess: invalidate,
  });
}

export function useDeleteRunbook(runbookId: string) {
  const invalidate = useInvalidateRunbooks();
  return useMutation({
    mutationFn: () => apiClient.delete<void>(`runbooks/${encodeURIComponent(runbookId)}`),
    onSuccess: invalidate,
  });
}

export function useUploadRunbooks() {
  const invalidate = useInvalidateRunbooks();
  return useMutation({
    mutationFn: (upload: {
      project_ids: string[];
      files: UploadedFile[];
      confirm_credentials: boolean;
    }) => apiClient.post<UploadResult>("runbooks/upload", upload),
    onSuccess: invalidate,
  });
}
