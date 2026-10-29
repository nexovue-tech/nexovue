import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/**
 * Which kind of install the console is running on (nexovue_platform.deployment): nexovue's
 * hosted service, or a customer's own. A self-hosted install has no billing, no plan
 * allowance, and no nexovue staff to grant support access to.
 */
export type Deployment = "hosted" | "self-hosted";

export interface SystemInfo {
  service: string;
  revision: string;
  build_time: string;
  deployment: Deployment;
}

export const systemInfoQueryKey = ["system", "info"] as const;

export function useSystemInfo() {
  return useQuery({
    queryKey: systemInfoQueryKey,
    queryFn: ({ signal }) => apiClient.get<SystemInfo>("system/info", { signal }),
    // A fact about the install, not the session: it does not change while the page is open.
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/**
 * Whether this is nexovue's hosted service. False until the answer arrives, so nothing
 * billing-shaped flashes on a customer's own install — the same rule `useCan` follows.
 */
export function useIsHosted(): boolean {
  return useSystemInfo().data?.deployment === "hosted";
}
