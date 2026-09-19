import { useQuery } from "@tanstack/react-query";

import { apiClient } from "./api-client";

/**
 * Who is signed in, and what they may do.
 *
 * Here rather than in `features/auth` because every feature asks it: a screen hides a
 * control the API would refuse, and a feature may not import another feature.
 */

/** The API's `auth/permissions.py` `Action` values. */
export type Action =
  | "operate_checks"
  | "operate_alerts"
  | "upload_source_maps"
  | "manage_channels"
  | "manage_check_secrets"
  | "configure_projects"
  | "manage_credentials"
  | "manage_members"
  | "manage_owners"
  | "read_audit"
  | "commit_spend"
  | "end_organization";

/** The identity half of the M1 contract. Shapes are the contract's, not ours. */

export interface Organization {
  id: string;
  slug: string;
  name: string;
}

export interface AuthenticatedUser {
  id: string;
  email: string;
  /** Nullable: users.name is nullable in the schema. */
  name: string | null;
  role: string;
  /**
   * ISO timestamp, null until the address is proven. Verification gates nothing in
   * Phase 1, so its absence is invisible without this field — it exists so the shell
   * knows whether there is anything to offer.
   */
  email_verified_at: string | null;
  /** What this person may do (`auth/permissions.py`); the console hides the rest. */
  permissions: Action[];
}

export interface Session {
  user: AuthenticatedUser;
  organization: Organization;
}

export const sessionQueryKey = ["auth", "me"] as const;

/**
 * Who is signed in, from the server rather than from what a screen remembered.
 *
 * `/auth/me` is deliberately off the API's strict auth rate-limit tier — its policy
 * comment says in as many words that it is polled by every signed-in screen — so the
 * shell may hold this open for as long as it is mounted. A 401 here is an expired
 * session, and app/query-client.ts is what turns it into a trip to /login.
 */
export function useSession() {
  return useQuery({
    queryKey: sessionQueryKey,
    queryFn: ({ signal }) => apiClient.get<Session>("auth/me", { signal }),
  });
}

/**
 * Whether the signed-in person may do `action`.
 *
 * False until the session has loaded: a control that appears and then vanishes is worse
 * than one that appears a moment late. The API refuses regardless — this only keeps a
 * person from being offered what they cannot do (F26, BR7).
 */
export function useCan(action: Action): boolean {
  const session = useSession();
  return session.data?.user.permissions.includes(action) ?? false;
}
