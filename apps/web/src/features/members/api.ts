import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { sessionQueryKey } from "@/shared/identity";

/** The members API (F26). Shapes are the API's `members/schemas.py`. */

export type Role = "owner" | "admin" | "member" | "viewer";
export type MemberStatus = "active" | "invited" | "expired";

export interface Member {
  id: string;
  email: string;
  name: string | null;
  role: Role;
  status: MemberStatus;
  invited_by: string | null;
  invited_at: string | null;
  /** Null for a pending invite means the email did not go; resend sends it. */
  invite_sent_at: string | null;
  invite_expires_at: string | null;
  added_at: string;
  last_signed_in_at: string | null;
}

export const membersQueryKey = ["members"] as const;

export function useMembers() {
  return useQuery({
    queryKey: membersQueryKey,
    queryFn: ({ signal }) => apiClient.get<{ members: Member[] }>("members", { signal }),
    select: (response) => response.members,
  });
}

function useInvalidating<Input, Output>(
  mutationKey: string,
  mutationFn: (input: Input) => Promise<Output>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["members", mutationKey],
    mutationFn,
    // Changing someone's role can change what the signed-in person may do too —
    // stepping down from owner — so the session is re-read with the list.
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: membersQueryKey }),
        queryClient.invalidateQueries({ queryKey: sessionQueryKey }),
      ]),
  });
}

export function useInvite() {
  return useInvalidating("invite", (input: { email: string; role: Role }) =>
    apiClient.post<Member>("members/invitations", input),
  );
}

export function useResendInvite() {
  return useInvalidating("resend", (memberId: string) =>
    apiClient.post<Member>(`members/invitations/${memberId}/resend`),
  );
}

export function useWithdrawInvite() {
  return useInvalidating("withdraw", (memberId: string) =>
    apiClient.delete<void>(`members/invitations/${memberId}`),
  );
}

export function useChangeRole() {
  return useInvalidating("change-role", (input: { memberId: string; role: Role }) =>
    apiClient.patch<Member>(`members/${input.memberId}`, { role: input.role }),
  );
}

export function useRemoveMember() {
  return useInvalidating("remove", (memberId: string) =>
    apiClient.delete<void>(`members/${memberId}`),
  );
}
