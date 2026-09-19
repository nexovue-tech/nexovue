import { ApiError, type ProblemDetails } from "@/shared/api-client";

/**
 * The staff admin API (F31, ADR-016): `/admin/v1`, a different app from the customer
 * API, reached with the staff cookie only. Errors are the shared `ApiError`, so the
 * shared failure components render them.
 */

const ADMIN_BASE_PATH = "/admin/v1/";

export async function adminRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const url = new URL(`${ADMIN_BASE_PATH}${path.replace(/^\/+/, "")}`, window.location.origin);
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("The admin API could not be reached.", 0, {
      traceId: "",
      correlationId: "",
      offline: true,
      aborted: false,
    });
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : undefined;
  if (!response.ok) {
    const problem = parsed as ProblemDetails | undefined;
    throw new ApiError(
      problem?.detail ?? `The admin API replied ${response.status}.`,
      response.status,
      {
        traceId: "",
        correlationId: response.headers.get("x-correlation-id") ?? "",
        offline: false,
        aborted: false,
      },
      problem,
    );
  }
  return parsed as T;
}

/** No staff session: the admin app's one reading of a status, as `query-client.ts` is the console's. */
export function isSignedOut(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export interface Staff {
  id: string;
  email: string;
  name: string;
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
