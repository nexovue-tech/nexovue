import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/** API tokens (F36). Shapes are the API's `apitokens/schemas.py`. */

export type TokenStatus = "live" | "revoked" | "expired";

export interface ApiTokenSummary {
  id: string;
  name: string;
  /** Null reads every project in the organization. */
  project_id: string | null;
  /** `nvt_live_…a9Qz`: what tells two tokens apart. The secret was shown once. */
  token_hint: string;
  /** Null once the person who made it has been removed; the token keeps working. */
  created_by_name: string | null;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
  requests_today: number;
  status: TokenStatus;
}

export interface ApiTokenCreated {
  api_token: ApiTokenSummary;
  /** The plaintext. Shown once, never stored, never cached. */
  token: string;
  notice: string;
}

export interface CreateTokenInput {
  name: string;
  project_id: string | null;
  expires_in_days: number;
}

export const apiTokensQueryKey = ["api-tokens"] as const;

export function useApiTokens() {
  return useQuery({
    queryKey: apiTokensQueryKey,
    queryFn: ({ signal }) =>
      apiClient.get<{ api_tokens: ApiTokenSummary[] }>("api-tokens", { signal }),
    select: (response) => response.api_tokens,
  });
}

/**
 * The list is invalidated rather than written into: the response carries the
 * secret, and putting it in the cache would keep it alive past the one screen
 * that is allowed to show it — the same reasoning as the ingest keys'.
 */
export function useCreateApiToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["api-tokens", "create"],
    mutationFn: (input: CreateTokenInput) => apiClient.post<ApiTokenCreated>("api-tokens", input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: apiTokensQueryKey }),
  });
}

export function useRevokeApiToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["api-tokens", "revoke"],
    mutationFn: (tokenId: string) =>
      apiClient.post<ApiTokenSummary>(`api-tokens/${tokenId}/revoke`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: apiTokensQueryKey }),
  });
}
