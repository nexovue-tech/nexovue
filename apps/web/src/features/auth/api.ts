import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { API_BASE_PATH, apiClient } from "@/shared/api-client";
import type { AuthenticatedUser, Organization, Session } from "@/shared/identity";
import { sessionQueryKey } from "@/shared/identity";

export type { AuthenticatedUser, Organization, Session } from "@/shared/identity";
export { sessionQueryKey, useSession } from "@/shared/identity";

/** The identity half of the M1 contract. Shapes are the contract's, not ours. */

export interface SignupRequest {
  organization_name: string;
  email: string;
  password: string;
  /** Optional, per the contract. Omitted rather than sent empty. */
  name?: string;
  /**
   * The browser's IANA zone (F6, D23), so the first project's days are the owner's
   * local days. A hint the API checks, and drops for UTC if it does not know it.
   */
  timezone?: string;
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

/**
 * What `POST /auth/login` answers when the password was right and is not enough.
 *
 * It names nothing about the account. Somebody who has guessed a password correctly
 * learns that and no more — no address, no organization, no display name.
 */
export interface SecondFactorChallenge {
  second_factor_required: true;
  challenge: string;
}

export type LoginOutcome = Session | SecondFactorChallenge;

/** Which of the two answers came back. A field, not the absence of other fields. */
export function needsSecondFactor(outcome: LoginOutcome): outcome is SecondFactorChallenge {
  return "second_factor_required" in outcome;
}

/**
 * Exchanges credentials for a session, *or* for a challenge.
 *
 * **No cookie is set on the challenge path.** A caller who has proved only the password
 * holds one factor of two and must not reach a single screen with it; the session is
 * minted by `submitSecondFactor` and nowhere else.
 */
export function login(credentials: { email: string; password: string }): Promise<LoginOutcome> {
  return apiClient.post<LoginOutcome>("auth/login", credentials);
}

/**
 * Finishes a sign-in with an authenticator code or a recovery code — one field for
 * both, because the customer knows either as "the code".
 *
 * Every failure is one 401 with one message: a challenge that never existed, one that
 * expired, one whose attempts are spent, and a wrong code alike. A screen that tried to
 * tell them apart would be inventing a distinction the API refuses to make.
 */
export function submitSecondFactor(submission: {
  challenge: string;
  code: string;
}): Promise<Session> {
  return apiClient.post<Session>("auth/login/second-factor", submission);
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
/**
 * Sign out of this browser, and only this one.
 *
 * The console had no such thing: the only way out was *Sign out everywhere*,
 * which ends the session on every phone and laptop the account is signed in on.
 * A shared machine is the ordinary case for wanting out of one of them.
 *
 * A hard navigation rather than a router push, for the reason the support view
 * uses one: the cookie is gone, every cached answer on the page was fetched with
 * it, and the cheapest way to be certain none of it is still on screen is to
 * start the app again.
 */
export function useSignOut() {
  return useMutation({
    mutationKey: ["auth", "sign-out"],
    mutationFn: () => apiClient.post<void>("auth/logout"),
    onSuccess: () => window.location.assign("/login"),
  });
}

export function useSignOutEverywhere() {
  return useMutation({
    mutationKey: ["profile", "sign-out-everywhere"],
    mutationFn: () => apiClient.post<void>("profile/sign-out-everywhere"),
  });
}

/* ------------------------------------------------------- what is signed in */

export interface ActiveSession {
  /**
   * The session's token fingerprint. Opaque here and safe to hold: it is a SHA-256
   * digest, and the API proves an id belongs to the caller before ending anything.
   */
  id: string;
  created_at: string;
  last_seen_at: string;
  /**
   * "Chrome on macOS", or null when the client said nothing recognisable. Coarse on
   * purpose — the API never stores the raw User-Agent, because this list is readable
   * by whoever holds a session, which on this screen may be the intruder.
   */
  client: string | null;
  /** The network, never the exact address: "203.0.113.0/24". Null when unknown. */
  network: string | null;
  /** The one the reader is sitting in. Computed by the API from the request's token. */
  current: boolean;
}

export const sessionsQueryKey = ["profile", "sessions"] as const;
export const twoFactorQueryKey = ["profile", "two-factor"] as const;

/**
 * What is signed in as you — the thing somebody actually wants when they suspect
 * another person is in their account. The list is the API's; no id is sent, because
 * the account is the one the session names.
 */
export function useActiveSessions() {
  return useQuery({
    queryKey: sessionsQueryKey,
    queryFn: ({ signal }) => apiClient.get<ActiveSession[]>("profile/sessions", { signal }),
  });
}

/**
 * End one session. A 404 means the id is not this account's — deliberately the same
 * answer as an id that never existed, so the endpoint cannot be used to find out that
 * somebody else's session is live.
 */
export function useEndSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["profile", "sessions", "end"],
    mutationFn: (id: string) => apiClient.delete<void>(`profile/sessions/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionsQueryKey }),
  });
}

/* ------------------------------------------------------ the second factor */

export interface SecondFactorState {
  enabled: boolean;
  confirmed_at: string | null;
  recovery_codes_remaining: number;
}

export interface SecondFactorEnrolment {
  /** The shared secret, for somebody typing it in by hand. Shown once. */
  secret: string;
  /** The same secret as an `otpauth://` URI — what a QR code would encode. */
  provisioning_uri: string;
}

/** Whether signing in as you needs a code, and how much cover is left. */
export function useSecondFactor() {
  return useQuery({
    queryKey: twoFactorQueryKey,
    queryFn: ({ signal }) => apiClient.get<SecondFactorState>("profile/two-factor", { signal }),
  });
}

/**
 * Asks for a secret and changes nothing on the account.
 *
 * **The only response in the API that carries the secret.** It is gated on the current
 * password, because arming a second factor on somebody else's account is a way to lock
 * them out of it. Nothing is switched on until `useConfirmSecondFactor` proves a code.
 */
export function useBeginSecondFactorEnrolment() {
  return useMutation({
    mutationKey: ["profile", "two-factor", "enrol"],
    mutationFn: (currentPassword: string) =>
      apiClient.post<SecondFactorEnrolment>("profile/two-factor/enrol", {
        current_password: currentPassword,
      }),
  });
}

/**
 * Proves a code and switches the factor on, answering with the recovery codes.
 *
 * They are shown once and there is no endpoint that returns them again, because there
 * is nothing to return: only hashes are stored.
 */
export function useConfirmSecondFactor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["profile", "two-factor", "confirm"],
    mutationFn: (code: string) =>
      apiClient.post<{ recovery_codes: string[] }>("profile/two-factor/confirm", { code }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: twoFactorQueryKey }),
  });
}

/** Turns it off, gated on the current password exactly as turning it on is. */
export function useDisableSecondFactor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["profile", "two-factor", "disable"],
    mutationFn: (currentPassword: string) =>
      apiClient.post<void>("profile/two-factor/disable", { current_password: currentPassword }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: twoFactorQueryKey }),
  });
}

/* ------------------------------------------------------------------ your data */

/**
 * A link rather than a fetch: the response is `Content-Disposition: attachment`, so the
 * browser saves it itself, and the session cookie travels with a same-origin link.
 */
export const PERSONAL_DATA_FILE_URL = `${API_BASE_PATH}profile/data.json`;

/** Whether this person may delete their account, and the API's sentence when not. */
export interface AccountDeletionState {
  allowed: boolean;
  refusal: string | null;
}

export const accountDeletionQueryKey = ["profile", "deletion"] as const;

export function useAccountDeletionState() {
  return useQuery({
    queryKey: accountDeletionQueryKey,
    queryFn: () => apiClient.get<AccountDeletionState>("profile/deletion"),
  });
}

export interface AccountDeletionProof {
  current_password: string;
  /** Only when two-factor is on; omitted rather than sent empty. */
  code?: string;
}

export function useDeleteAccount() {
  return useMutation({
    mutationKey: ["profile", "deletion"],
    mutationFn: (proof: AccountDeletionProof) => apiClient.post<void>("profile/deletion", proof),
  });
}

/* ------------------------------------------------------------------ invitations */

/** What an invite link is for, before anyone types a password (F26). */
export interface InvitationPreview {
  organization: string;
  email: string;
  role: "owner" | "admin" | "member" | "viewer";
  invited_by: string;
  expires_at: string;
}

/** A POST, so the link's token travels in a body rather than a logged URL. */
export function previewInvitation(token: string) {
  return apiClient.post<InvitationPreview>("invitations/preview", { token });
}

/** Sets the password, proves the address and signs the person in. */
export function acceptInvitation(input: { token: string; name: string; password: string }) {
  return apiClient.post<Session>("invitations/accept", input);
}

/** Whether this deployment lets anyone create an organization (F31, FR30). */
export function signupStatus() {
  return apiClient.get<{ enabled: boolean }>("system/signup");
}

export const signupStatusQueryKey = ["system", "signup"] as const;
