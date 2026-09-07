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
