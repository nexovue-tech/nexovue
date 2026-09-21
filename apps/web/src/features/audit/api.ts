import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/** The audit log read (F26, FR36). Shapes are the API's `audit/schemas.py`. */

export type EventKind =
  | "people"
  | "credentials"
  | "sign_in"
  | "configuration"
  | "support"
  | "changes";

export interface AuditActor {
  kind: "person" | "staff" | "system";
  id: string | null;
  name: string | null;
}

export interface AuditEntry {
  id: string;
  at: string;
  actor: AuditActor;
  action: string;
  target: string;
  target_name: string | null;
  detail: Record<string, unknown> | null;
  impersonation_context: string | null;
}

interface AuditPage {
  entries: AuditEntry[];
  next_cursor: string | null;
}

export interface AuditFilters {
  actor?: string;
  kind?: EventKind;
  /** ISO instant; entries at or after it. */
  from?: string;
}

export function useAuditLog(filters: AuditFilters, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ["audit", filters],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiClient.get<AuditPage>("audit", {
        searchParams: { ...filters, cursor: pageParam },
        signal,
      }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

/** The people to filter by: everyone in the organization, removed people aside. */
export function usePeople(enabled: boolean) {
  return useQuery({
    queryKey: ["audit", "people"],
    enabled,
    queryFn: ({ signal }) =>
      apiClient.get<{ members: { id: string; name: string | null; email: string }[] }>("members", {
        signal,
      }),
    select: (response) => response.members,
  });
}
