import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/** Support access (F31). Shapes are the API's `support/schemas.py`. */

export type GrantHours = 24 | 72 | 168;

export interface SupportViewRecord {
  at: string;
  /** "Raj at nexovue". */
  staff: string;
  /** Who was viewed, as recorded when the view started. */
  viewed: string | null;
  reason: string | null;
  impersonation_id: string | null;
}

export interface SupportAccessState {
  /** Null when support may not view; the moment access lapses otherwise. */
  granted_until: string | null;
  views: SupportViewRecord[];
}

export const supportAccessQueryKey = ["support-access"] as const;

export function useSupportAccess(enabled: boolean) {
  return useQuery({
    queryKey: supportAccessQueryKey,
    queryFn: ({ signal }) => apiClient.get<SupportAccessState>("support-access", { signal }),
    enabled,
  });
}

function useChangingAccess<Input>(mutationKey: string, run: (input: Input) => Promise<void>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["support-access", mutationKey],
    mutationFn: run,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: supportAccessQueryKey }),
  });
}

export function useGrantSupportAccess() {
  return useChangingAccess("grant", (hours: GrantHours) =>
    apiClient.post<void>("support-access", { hours }),
  );
}

export function useWithdrawSupportAccess() {
  return useChangingAccess("withdraw", () => apiClient.delete<void>("support-access"));
}

/** Ends a support view. Logout is the one write a support view may make. */
export function endSupportView(): Promise<void> {
  return apiClient.post<void>("auth/logout");
}
