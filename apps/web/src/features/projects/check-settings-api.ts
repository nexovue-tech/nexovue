import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * What a project's checks draw on: secrets, variables and maintenance windows.
 * Field names are the API's.
 *
 * **A secret's value only travels one way.** It is sent in a `PUT` and no response
 * carries it back (FR20), so nothing below has a type with a secret's value in it.
 * A screen that wanted to show one would have nothing to show.
 */

/* The rules `checksecrets/schemas.py` and `nexovue_platform.checks.templating` enforce,
   checked here first so the field can say what is wrong before a request is spent. */
const SECRET_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const VARIABLE_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;
export const MIN_SECRET_LENGTH = 4;
export const MAX_SECRET_LENGTH = 8192;
export const MAX_VARIABLE_LENGTH = 8192;
export const MAX_SECRETS_PER_PROJECT = 100;
export const MAX_VARIABLES_PER_PROJECT = 100;

export interface CheckSecretSummary {
  name: string;
  created_at: string;
  updated_at: string;
  /** How many of the project's checks refer to it, so a delete can say what breaks. */
  used_by_checks: number;
}

export interface CheckVariableSummary {
  name: string;
  value: string;
  updated_at: string;
  used_by_checks: number;
}

export type Recurrence = "none" | "daily" | "weekly";

export interface MaintenanceWindow {
  id: string;
  project_id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  recurrence: Recurrence;
  /** The last moment a repeating window may begin. Null repeats indefinitely. */
  repeat_until: string | null;
  /** True covers every check in the project, including checks created later. */
  applies_to_all: boolean;
  check_ids: string[];
  active_now: boolean;
  created_at: string;
}

/** The whole window. An edit replaces it, including which checks it covers. */
export interface MaintenanceWindowRequest {
  project_id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  recurrence: Recurrence;
  repeat_until: string | null;
  applies_to_all: boolean;
  check_ids: string[];
}

/** Only what choosing a check needs; the list endpoint returns far more. */
export interface CheckChoice {
  id: string;
  name: string;
  type: string;
}

interface CheckSecretListResponse {
  secrets: CheckSecretSummary[];
}

interface CheckVariableListResponse {
  variables: CheckVariableSummary[];
}

interface MaintenanceWindowListResponse {
  windows: MaintenanceWindow[];
}

interface CheckListResponse {
  checks: CheckChoice[];
}

/* ------------------------------------------------------------ pure helpers */

/** How a check refers to a secret. */
export function secretReference(name: string): string {
  return `{{secret.${name}}}`;
}

/** How a check refers to a variable. */
export function variableReference(name: string): string {
  return `{{${name}}}`;
}

/** Undefined for an empty name: an empty field is incomplete, not wrong. */
export function secretNameProblem(name: string): string | undefined {
  if (name === "" || SECRET_NAME_PATTERN.test(name)) return undefined;
  return "Letters, digits and underscores, starting with a letter or underscore — at most 64.";
}

export function variableNameProblem(name: string): string | undefined {
  if (name === "" || VARIABLE_NAME_PATTERN.test(name)) return undefined;
  return "Letters, digits, underscores, dots and hyphens, starting with a letter or underscore — at most 64.";
}

/**
 * A secret shorter than the minimum is refused because every captured string is
 * redacted of it, and a three-character value would blank unrelated text everywhere.
 */
export function secretValueProblem(value: string): string | undefined {
  if (value === "") return undefined;
  if (value.length < MIN_SECRET_LENGTH) {
    return `At least ${MIN_SECRET_LENGTH} characters, so redacting it does not blank ordinary text.`;
  }
  if (value.length > MAX_SECRET_LENGTH) return `At most ${MAX_SECRET_LENGTH} characters.`;
  return undefined;
}

export function variableValueProblem(value: string): string | undefined {
  return value.length > MAX_VARIABLE_LENGTH
    ? `At most ${MAX_VARIABLE_LENGTH} characters.`
    : undefined;
}

/** Names are unique per project, and `PUT` on an existing one replaces it. */
export function isTaken(name: string, existing: readonly { name: string }[]): boolean {
  return existing.some((item) => item.name === name);
}

export function byName<T extends { name: string }>(items: readonly T[]): T[] {
  return [...items].sort((left, right) => left.name.localeCompare(right.name));
}

/* ------------------------------------------------------------------ queries */

export const checkSecretsQueryKey = (projectId: string) => ["check-secrets", projectId] as const;
export const checkVariablesQueryKey = (projectId: string) =>
  ["check-variables", projectId] as const;
export const maintenanceWindowsQueryKey = (projectId: string) =>
  ["maintenance-windows", projectId] as const;
/* Not the synthetics screen's `["checks", …]` key: that cache holds its own response
   shape, and two features writing different shapes under one key would corrupt both. */
export const checkChoicesQueryKey = (projectId: string) =>
  ["maintenance-check-choices", projectId] as const;
/* The synthetics screens' check list, which says whether each check is in maintenance.
   Written out because a feature may not import another's key. */
const checkListQueryKey = (projectId: string) => ["checks", projectId] as const;

const secretPath = (projectId: string, name: string) =>
  `projects/${projectId}/check-secrets/${encodeURIComponent(name)}`;
const variablePath = (projectId: string, name: string) =>
  `projects/${projectId}/check-variables/${encodeURIComponent(name)}`;

export function useCheckSecrets(projectId: string) {
  return useQuery({
    queryKey: checkSecretsQueryKey(projectId),
    queryFn: ({ signal }) =>
      apiClient.get<CheckSecretListResponse>(`projects/${projectId}/check-secrets`, { signal }),
    select: (response) => response.secrets,
  });
}

export interface SecretWriteInput {
  name: string;
  value: string;
}

export function useWriteCheckSecret(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["check-secrets", "write"],
    // A mutation keeps its variables, and these carry the plaintext: drop them as soon
    // as nothing is watching rather than holding them for the default five minutes.
    gcTime: 0,
    mutationFn: ({ name, value }: SecretWriteInput) =>
      apiClient.put<CheckSecretSummary>(secretPath(projectId, name), { value }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: checkSecretsQueryKey(projectId) }),
  });
}

export function useDeleteCheckSecret(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["check-secrets", "delete"],
    mutationFn: (name: string) => apiClient.delete<void>(secretPath(projectId, name)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: checkSecretsQueryKey(projectId) }),
  });
}

export function useCheckVariables(projectId: string) {
  return useQuery({
    queryKey: checkVariablesQueryKey(projectId),
    queryFn: ({ signal }) =>
      apiClient.get<CheckVariableListResponse>(`projects/${projectId}/check-variables`, {
        signal,
      }),
    select: (response) => response.variables,
  });
}

export interface VariableWriteInput {
  name: string;
  value: string;
}

export function useWriteCheckVariable(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["check-variables", "write"],
    mutationFn: ({ name, value }: VariableWriteInput) =>
      apiClient.put<CheckVariableSummary>(variablePath(projectId, name), { value }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: checkVariablesQueryKey(projectId) }),
  });
}

export function useDeleteCheckVariable(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["check-variables", "delete"],
    mutationFn: (name: string) => apiClient.delete<void>(variablePath(projectId, name)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: checkVariablesQueryKey(projectId) }),
  });
}

export function useMaintenanceWindows(projectId: string) {
  return useQuery({
    queryKey: maintenanceWindowsQueryKey(projectId),
    queryFn: ({ signal }) =>
      apiClient.get<MaintenanceWindowListResponse>("maintenance-windows", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.windows,
  });
}

export function useCheckChoices(projectId: string) {
  return useQuery({
    queryKey: checkChoicesQueryKey(projectId),
    queryFn: ({ signal }) =>
      apiClient.get<CheckListResponse>("checks", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response): CheckChoice[] =>
      response.checks.map(({ id, name, type }) => ({ id, name, type })),
  });
}

export interface MaintenanceWindowSaveInput {
  /** Absent creates a window; present replaces that one. */
  windowId?: string;
  request: MaintenanceWindowRequest;
}

export function useSaveMaintenanceWindow(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["maintenance-windows", "save"],
    mutationFn: ({ windowId, request }: MaintenanceWindowSaveInput) =>
      windowId === undefined
        ? apiClient.post<MaintenanceWindow>("maintenance-windows", request)
        : apiClient.put<MaintenanceWindow>(`maintenance-windows/${windowId}`, request),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: maintenanceWindowsQueryKey(projectId) }),
        queryClient.invalidateQueries({ queryKey: checkListQueryKey(projectId) }),
      ]),
  });
}

export function useDeleteMaintenanceWindow(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["maintenance-windows", "delete"],
    mutationFn: (windowId: string) => apiClient.delete<void>(`maintenance-windows/${windowId}`),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: maintenanceWindowsQueryKey(projectId) }),
        queryClient.invalidateQueries({ queryKey: checkListQueryKey(projectId) }),
      ]),
  });
}
