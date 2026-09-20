import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/shared/api-client";

/**
 * The platform surface (`docs/changes/super-admin-in-the-console/`).
 *
 * Ordinary console calls on the ordinary session: the separation is no longer an
 * app and a cookie, it is the role plus a re-authentication no older than thirty
 * minutes. The API answers 403 with `NEEDS_RE_AUTHENTICATION` when that lapses,
 * which `needsReAuthentication` recognises so a screen can ask rather than fail.
 */

const NEEDS_RE_AUTHENTICATION = "has not been re-verified";

export function needsReAuthentication(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.problem?.detail ?? "").includes(NEEDS_RE_AUTHENTICATION)
  );
}

export interface OrganizationRow {
  id: string;
  slug: string;
  name: string;
  region: string;
  plan: string;
  created_at: string;
  deleted: boolean;
  suspended: boolean;
}

export interface OrganizationPage {
  organization: OrganizationRow;
  suspension: { at: string; reason: string } | null;
  plan_grant: {
    id: string;
    plan: string;
    services: number;
    ends_at: string;
    reason: string;
  } | null;
  members: { id: string; email: string; name: string | null; role: string; status: string }[];
  projects: { id: string; slug: string; name: string; last_ingest_day: string | null }[];
  usage: {
    plan: { slug: string; name: string };
    period: { from_day: string; to_day: string };
  } | null;
}

export const organizationsKey = (q: string) => ["platform", "organizations", q] as const;
export const organizationKey = (id: string) => ["platform", "organization", id] as const;

export function useOrganizations(q: string) {
  return useQuery({
    queryKey: organizationsKey(q),
    queryFn: ({ signal }) =>
      apiClient.get<{ organizations: OrganizationRow[] }>("admin/organizations", {
        searchParams: { q },
        signal,
      }),
    select: (response) => response.organizations,
    enabled: q.trim().length > 1,
    retry: false,
  });
}

export function useOrganization(orgId: string) {
  return useQuery({
    queryKey: organizationKey(orgId),
    queryFn: ({ signal }) =>
      apiClient.get<OrganizationPage>(`admin/organizations/${orgId}`, { signal }),
    retry: false,
  });
}

function useOnOrganization<Input, Output = unknown>(
  orgId: string,
  run: (input: Input) => Promise<Output>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: organizationKey(orgId) }),
  });
}

export function useSuspend(orgId: string) {
  return useOnOrganization(orgId, (reason: string) =>
    apiClient.post<void>(`admin/organizations/${orgId}/suspension`, { reason }),
  );
}

export function useResume(orgId: string) {
  return useOnOrganization(orgId, () =>
    apiClient.delete<void>(`admin/organizations/${orgId}/suspension`),
  );
}

export interface GrantInput {
  plan: string;
  services: number;
  ends_on: string;
  reason: string;
}

export function useGrantPlan(orgId: string) {
  return useOnOrganization(orgId, (input: GrantInput) =>
    apiClient.post<void>(`admin/organizations/${orgId}/plan-grant`, input),
  );
}

export function useEndGrant(orgId: string) {
  return useOnOrganization(orgId, () =>
    apiClient.delete<void>(`admin/organizations/${orgId}/plan-grant`),
  );
}

export interface OwnerLinkSent {
  organization_id: string;
  /** False when the link was made and the mail provider refused it. */
  owner_link_sent: boolean;
}

export function useResendOwnerLink(orgId: string) {
  return useOnOrganization<void, OwnerLinkSent>(orgId, () =>
    apiClient.post<OwnerLinkSent>(`admin/organizations/${orgId}/owner-link`),
  );
}

export function useViewAs(orgId: string) {
  return useMutation({
    mutationFn: (input: { user_id: string; reason: string }) =>
      apiClient.post<{ url: string }>(`admin/organizations/${orgId}/view-as`, input),
  });
}

export interface OnboardInput {
  organization_name: string;
  owner_email: string;
  owner_name: string | null;
  region: string;
  payment: { razorpay_payment_id: string; amount_minor: number; currency: string };
}

export function useOnboard() {
  return useMutation({
    mutationFn: (input: OnboardInput) =>
      apiClient.post<{ organization_id: string; owner_link_sent: boolean }>(
        "admin/organizations",
        input,
      ),
  });
}

/** Confirming it is you: what re-opens the platform for thirty minutes. */
export function useEnterAdminMode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["platform", "admin-mode"],
    mutationFn: (proof: { password?: string; code?: string }) =>
      apiClient.post<{ until: string }>("auth/admin-mode", proof),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platform"] }),
  });
}
