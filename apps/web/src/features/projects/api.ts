import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * The project endpoints. Field names are the API's.
 *
 * **Both config documents are replaced whole, never merged.** `PUT` on either
 * one overwrites what is stored, so a screen that renders half a document and
 * saves it deletes the half it never drew. That is why `MaskingDocument` below
 * spells every key `projects/config.py` accepts rather than only the ones the
 * form has controls for — a key nobody edits still has to make the round trip.
 */

export interface ProjectSummary {
  id: string;
  slug: string;
  name: string;
}

/**
 * The five rule families the masking worker ships with, spelled as
 * `config.MASKING_RULE_NAMES` spells them. A name here the worker does not know
 * is a rule the API accepts and the worker rejects.
 */
export const MASKING_RULE_NAMES = [
  "credential_keys",
  "authorization_headers",
  "cookies",
  "card_numbers",
  "authorization_credentials",
] as const;

export type MaskingRuleName = (typeof MASKING_RULE_NAMES)[number];

/** The keys whose entries are the customer's own attribute keys and values. */
export const MASKING_TEXT_KEYS = [
  "mask_keys",
  "mask_key_contains",
  "hash_keys",
  "hash_key_contains",
  "allow_values",
] as const;

export type MaskingTextKey = (typeof MASKING_TEXT_KEYS)[number];

export type MaskingDocument = Partial<Record<MaskingTextKey, string[]>> & {
  /** Rule families switched off entirely. The one edit that widens what leaves the process. */
  disable?: string[];
  /** Rule families whose matches become a pseudonym rather than a placeholder. */
  hash?: string[];
};

export interface SamplingDocument {
  keep_errors?: boolean;
  slow_threshold_ms?: number;
  healthy_percent?: number;
}

export interface ProjectDetail {
  id: string;
  slug: string;
  name: string;
  /** Resolved against the plan's ceiling by the API. Read-only here on purpose. */
  retention_days: number;
  sampling_config: SamplingDocument;
  masking_config: MaskingDocument;
  created_at: string;
}

/**
 * What deleting a project did, which is why the endpoint answers 200 with a body
 * rather than 204. Every count is a credential that stopped working.
 */
export interface ProjectDeleted {
  project_id: string;
  deleted_at: string;
  api_keys_revoked: number;
  site_keys_revoked: number;
  webhook_secrets_revoked: number;
  telemetry_notice: string;
}

interface ProjectListResponse {
  projects: ProjectSummary[];
}

export const projectsQueryKey = ["projects"] as const;
export const projectQueryKey = (projectId: string | undefined) => ["projects", projectId] as const;

export function useProjects() {
  return useQuery({
    queryKey: projectsQueryKey,
    queryFn: ({ signal }) => apiClient.get<ProjectListResponse>("projects", { signal }),
    select: (response) => response.projects,
  });
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: projectQueryKey(projectId),
    queryFn: ({ signal }) => apiClient.get<ProjectDetail>(`projects/${projectId}`, { signal }),
    enabled: projectId !== undefined,
  });
}

export interface ProjectCreateInput {
  name: string;
  /** Omitted lets the API derive one from the name, which is the ordinary path. */
  slug?: string;
}

export function useCreateProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["projects", "create"],
    mutationFn: (input: ProjectCreateInput) =>
      apiClient.post<ProjectDetail>("projects", {
        name: input.name,
        ...(input.slug ? { slug: input.slug } : {}),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: projectsQueryKey }),
  });
}

export interface ProjectRenameInput {
  projectId: string;
  name?: string;
  slug?: string;
}

export function useRenameProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["projects", "rename"],
    mutationFn: ({ projectId, ...fields }: ProjectRenameInput) =>
      apiClient.patch<ProjectDetail>(`projects/${projectId}`, fields),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: projectsQueryKey });
      queryClient.invalidateQueries({ queryKey: projectQueryKey(project.id) });
    },
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["projects", "delete"],
    mutationFn: (projectId: string) => apiClient.delete<ProjectDeleted>(`projects/${projectId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: projectsQueryKey }),
  });
}

export function useSaveSamplingConfig(projectId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["projects", "sampling-config"],
    mutationFn: (config: SamplingDocument) =>
      apiClient.put(`projects/${projectId}/sampling-config`, { config }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: projectQueryKey(projectId) }),
  });
}

export interface MaskingSaveInput {
  config: MaskingDocument;
  /**
   * Rule families being switched off that were not off before. The API refuses
   * the write unless each one is named here — ADR 0105 guards the off switch and
   * nothing else, and it records an audit row for every name.
   */
  confirmDisabling: string[];
}

export function useSaveMaskingConfig(projectId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["projects", "masking-config"],
    mutationFn: ({ config, confirmDisabling }: MaskingSaveInput) =>
      apiClient.put(`projects/${projectId}/masking-config`, {
        config,
        confirm_disabling: confirmDisabling,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: projectQueryKey(projectId) }),
  });
}
