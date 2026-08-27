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

export const sessionQueryKey = ["auth", "me"] as const;

export function login(credentials: { email: string; password: string }): Promise<Session> {
  return apiClient.post<Session>("auth/login", credentials);
}

export function signup(request: SignupRequest): Promise<SignupResponse> {
  return apiClient.post<SignupResponse>("signup", request);
}
