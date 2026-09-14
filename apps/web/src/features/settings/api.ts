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
  /** Fixed by the API. A key's kind is which table it came from, never a guess. */
  kind: "secret";
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

/**
 * A public browser key. The mirror image of the type above, and the differences
 * are the point.
 *
 * `public_key` is the whole key, present on every read, because this value lives
 * in the customer's own page source: there is no hint to show instead, nothing
 * to reveal once, and no reason to withhold it. It is safe to render, safe to
 * copy and safe to log — which is exactly why it must never be styled like the
 * secret one.
 */
export interface SiteKeySummary {
  kind: "public";
  id: string;
  project_id: string;
  name: string;
  public_key: string;
  /** Where this key may report from. Empty would accept nothing. */
  allowed_origins: string[];
  created_at: string;
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

/* ------------------------------------------------------- public browser keys */

/**
 * Site keys reach a different endpoint, not the same one with a `kind` on it, so
 * a request for the secret list can never answer with a public key. The query
 * cache is keyed separately for the same reason.
 *
 * Unlike the secret create, the response here goes straight into the cache: it
 * carries nothing that must not be kept, because the key it carries is one the
 * customer is about to paste into a public web page.
 */
export interface SiteKeyCreateInput {
  name: string;
  allowedOrigins: string[];
}

interface SiteKeyListResponse {
  site_keys: SiteKeySummary[];
}

export const siteKeysQueryKey = (projectId: string | undefined) =>
  ["site-keys", projectId] as const;

export function useSiteKeys(projectId: string | undefined) {
  return useQuery({
    queryKey: siteKeysQueryKey(projectId),
    queryFn: ({ signal }) =>
      apiClient.get<SiteKeyListResponse>("site-keys", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.site_keys,
    enabled: projectId !== undefined,
  });
}

export function useCreateSiteKey(projectId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["site-keys", "create"],
    mutationFn: ({ name, allowedOrigins }: SiteKeyCreateInput) =>
      apiClient.post<SiteKeySummary>("site-keys", {
        project_id: projectId,
        name,
        allowed_origins: allowedOrigins,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: siteKeysQueryKey(projectId) }),
  });
}

export function useRevokeSiteKey(projectId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["site-keys", "revoke"],
    mutationFn: (keyId: string) => apiClient.post<SiteKeySummary>(`site-keys/${keyId}/revoke`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: siteKeysQueryKey(projectId) }),
  });
}

/**
 * Where telemetry goes, which is a fact about the deployment rather than about a key.
 *
 * **It was reachable exactly once.** Signup returned it and the onboarding screen printed
 * it; nothing else in the product ever showed it again, so a customer who closed that tab
 * could not recover the one value their exporter needs. It belongs beside the keys,
 * because a key and an endpoint are useless apart.
 */
export interface IngestEndpoint {
  otlp_endpoint: string;
  key_header: string;
}

export function useIngestEndpoint() {
  return useQuery({
    queryKey: ["system", "ingest"] as const,
    // A deployment fact: it does not change while somebody is looking at it.
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: ({ signal }) => apiClient.get<IngestEndpoint>("system/ingest", { signal }),
  });
}
