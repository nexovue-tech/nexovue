import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * The ingest-key endpoints. Field names are the API's.
 *
 * `key` on a create response is the only time the secret exists outside the
 * customer's own configuration — we store a SHA-256 hash of it and nothing else,
 * so there is no endpoint that could return it a second time. It is therefore
 * never written to a query cache: the screen holds it in component state, where
 * it dies with the screen, rather than in a store that survives navigation.
 */

export interface ApiKeySummary {
  id: string;
  project_id: string;
  name: string;
  /** Scheme plus the last four characters of the stored prefix. Not a secret. */
  key_hint: string;
  created_at: string;
  /** Null until the receiver has seen the key authenticate. Coarse by design. */
  last_used_at: string | null;
  revoked_at: string | null;
}

interface ApiKeyListResponse {
  api_keys: ApiKeySummary[];
}

export interface ApiKeyCreated {
  api_key: ApiKeySummary;
  /** The plaintext. Shown once, never stored, never cached. */
  key: string;
  notice: string;
}

export const apiKeysQueryKey = (projectId: string | undefined) => ["api-keys", projectId] as const;

export function useApiKeys(projectId: string | undefined) {
  return useQuery({
    queryKey: apiKeysQueryKey(projectId),
    queryFn: ({ signal }) =>
      apiClient.get<ApiKeyListResponse>("api-keys", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.api_keys,
    enabled: projectId !== undefined,
  });
}

/**
 * Minting leaves every existing key live, which is what makes rotation possible.
 * The list is invalidated rather than written into: the response carries the
 * secret, and putting it in the cache would keep it alive past the one screen
 * that is allowed to show it.
 */
export function useCreateApiKey(projectId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["api-keys", "create"],
    mutationFn: (name: string) =>
      apiClient.post<ApiKeyCreated>("api-keys", { project_id: projectId, name }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: apiKeysQueryKey(projectId) }),
  });
}

export function useRevokeApiKey(projectId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["api-keys", "revoke"],
    mutationFn: (keyId: string) => apiClient.post<ApiKeySummary>(`api-keys/${keyId}/revoke`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: apiKeysQueryKey(projectId) }),
  });
}
