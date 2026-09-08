import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

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
}

export interface Session {
  user: AuthenticatedUser;
  organization: Organization;
}

export interface SignupRequest {
  organization_name: string;
  email: string;
  password: string;
  /** Optional, per the contract. Omitted rather than sent empty. */
  name?: string;
}

export interface SignupResponse {
  organization: Organization;
  project: { id: string; slug: string; name: string };
  /** Shown once. Never retrievable again. */
  api_key: string;
  otlp_endpoint: string;
}

export interface PasswordResetConfirmation {
  token: string;
  password: string;
}

/**
 * The API is the authority on this — `PASSWORD_MIN_LENGTH` in the API's auth/schemas.py
 * refuses anything shorter with a field error. Restated here only so a form can say the
 * rule before somebody trips over it.
 */
export const PASSWORD_MIN_LENGTH = 8;

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

export function login(credentials: { email: string; password: string }): Promise<Session> {
  return apiClient.post<Session>("auth/login", credentials);
}

export function signup(request: SignupRequest): Promise<SignupResponse> {
  return apiClient.post<SignupResponse>("signup", request);
}

/* ------------------------------------------------------------------- recovery */

/**
 * The three unauthenticated recovery calls. Unauthenticated because the customer clicks
 * the link in a mail client, which is a different browser session from the one they
 * signed up in — often a different device.
 *
 * Every one of them answers with no body: 204 for the two that finish something, 202 for
 * the one that only hands mail to a provider.
 */

/** Spends a single-use token. Calling it twice with the same token fails the second time. */
export function verifyEmail(token: string): Promise<void> {
  return apiClient.post<void>("auth/verify-email", { token });
}

/**
 * Always 202, for a known and an unknown address alike. A caller that branches on the
 * answer has turned the form back into the account-existence oracle this prevents.
 */
export function requestPasswordReset(email: string): Promise<void> {
  return apiClient.post<void>("auth/password-reset", { email });
}

/**
 * Sets the password and ends every live session. It issues no session back on purpose:
 * signing in with the new password is the step that proves to the customer it worked.
 */
export function confirmPasswordReset(reset: PasswordResetConfirmation): Promise<void> {
  return apiClient.post<void>("auth/password-reset/confirm", reset);
}

/**
 * Mails a fresh verification link, authenticated, to the signed-in account's own
 * address. The body carries no recipient on purpose: a caller-chosen one would let
 * anybody send mail from our verified domain wherever they liked.
 *
 * 202, not 204 — the mail has been handed to a provider, not delivered. Nothing on
 * screen may claim more than that. An address that is already verified answers 202 and
 * sends nothing.
 */
export function resendVerificationEmail(): Promise<void> {
  return apiClient.post<void>("auth/verify-email/resend");
}

/* --------------------------------------------------------- the account itself */

/**
 * The three writes a person can make to their own account. No route takes a user id:
 * the account being changed is the caller's, taken from the session, so a body that
 * could name one would be a body that could be forged.
 */

export interface PasswordChangeInput {
  current_password: string;
  new_password: string;
}

/**
 * Renames the caller and writes the answer straight into the session cache, because the
 * answer *is* the identity body every screen reads.
 *
 * `null` and an empty name are the same thing to the API — the column's own NULL — so
 * the screen sends null rather than `""`.
 */
export function useRenameSelf() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["profile", "rename"],
    mutationFn: (name: string | null) =>
      apiClient.patch<AuthenticatedUser>("profile", { name: name?.trim() || null }),
    onSuccess: (user) =>
      queryClient.setQueryData<Session>(sessionQueryKey, (session) =>
        session ? { ...session, user } : session,
      ),
  });
}

/**
 * Changes the password and ends every *other* session on the account.
 *
 * This session survives, and the 204 carries a replacement cookie: the caller proved
 * they hold the current password, which is the one thing a stolen session cannot do, so
 * signing them out of the device in front of them protects nobody. Nothing to do with
 * the response but say so.
 */
export function useChangeOwnPassword() {
  return useMutation({
    mutationKey: ["profile", "password"],
    mutationFn: (change: PasswordChangeInput) => apiClient.post<void>("profile/password", change),
  });
}

/**
 * Ends every session on the account *including this one*, and the 204 clears the cookie.
 * The caller is signed out here as well, which is why the screen navigates to /login on
 * success rather than treating the next 401 as a lapse.
 */
export function useSignOutEverywhere() {
  return useMutation({
    mutationKey: ["profile", "sign-out-everywhere"],
    mutationFn: () => apiClient.post<void>("profile/sign-out-everywhere"),
  });
}
