/**
 * The only way the browser talks to our API.
 *
 * fetch, not axios: axios is refused by biome.json after its npm account compromise in
 * March 2026, and nothing here needs more than the platform gives.
 *
 * Every request carries a W3C `traceparent` so a screen, an API span and a receiver
 * span join up — we are customer zero. Every failure arrives as one typed error, so
 * callers never branch on `response.ok`, and status handling lives in exactly one place
 * (app/query-client.ts), never in a feature.
 */

import { logger } from "./logger";

export const API_BASE_PATH = "/api/v1/";

const DEFAULT_TIMEOUT_MS = 20_000;
const PROBLEM_CONTENT_TYPE = "application/problem+json";

/** RFC 9457 problem details. Extension members are carried through untouched. */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  [extension: string]: unknown;
}

export interface ApiErrorContext {
  /** The traceparent trace id we generated for the request. Always present. */
  traceId: string;
  /** Server-supplied correlation id when there is one; the trace id otherwise. */
  correlationId: string;
  /** Seconds, from Retry-After, when the server asked us to wait. */
  retryAfterSeconds?: number;
  /** True when the request never reached the server. */
  offline: boolean;
  /** True when our own timeout or an abort signal ended the request. */
  aborted: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem?: ProblemDetails;
  readonly context: ApiErrorContext;

  constructor(message: string, status: number, context: ApiErrorContext, problem?: ProblemDetails) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
    this.context = context;
  }
}

const HTTP_NOT_FOUND = 404;

/**
 * True when the API answered "no such resource in this organization".
 *
 * Lives here rather than in a feature so no screen carries a bare status number: what a
 * 404 *means* is the resource's business, but recognising one is the client's. It says
 * nothing about why the resource is absent — the API deliberately answers the same way
 * for a row that never existed and a row belonging to another tenant.
 */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === HTTP_NOT_FOUND;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  /** Serialised as JSON. Use `formData` for uploads. */
  body?: unknown;
  formData?: FormData;
  searchParams?: Record<string, string | number | boolean | undefined>;
  headers?: Record<string, string>;
  timeoutMs?: number;
  signal?: AbortSignal;
}

/* ------------------------------------------------------------------- tracing */

const randomHex = (bytes: number) =>
  Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");

/** W3C Trace Context: version-traceid-spanid-flags. `01` = sampled. */
function newTraceparent() {
  const traceId = randomHex(16);
  return { traceId, header: `00-${traceId}-${randomHex(8)}-01` };
}

/* --------------------------------------------------------------- url building */

function buildUrl(path: string, searchParams: RequestOptions["searchParams"]) {
  const url = new URL(`${API_BASE_PATH}${path.replace(/^\/+/, "")}`, window.location.origin);
  for (const [key, value] of Object.entries(searchParams ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  return url;
}

/* ------------------------------------------------------------ failure parsing */

function isProblemDetails(value: unknown): value is ProblemDetails {
  return typeof value === "object" && value !== null && "title" in value && "status" in value;
}

async function readProblem(response: Response): Promise<ProblemDetails | undefined> {
  if (!response.headers.get("content-type")?.includes(PROBLEM_CONTENT_TYPE)) return undefined;
  try {
    const body: unknown = await response.json();
    return isProblemDetails(body) ? body : undefined;
  } catch {
    return undefined; // A malformed problem body is still a failure; it just tells us less.
  }
}

/** Retry-After is either delta-seconds or an HTTP date (RFC 9110 §10.2.3). */
function parseRetryAfter(header: string | null): number | undefined {
  if (!header) return undefined;

  const seconds = Number(header);
  if (Number.isFinite(seconds)) return Math.max(0, seconds);

  const until = Date.parse(header);
  return Number.isNaN(until) ? undefined : Math.max(0, Math.round((until - Date.now()) / 1000));
}

function correlationIdFrom(
  response: Response,
  problem: ProblemDetails | undefined,
  fallback: string,
) {
  const fromProblem = problem?.correlation_id;
  if (typeof fromProblem === "string" && fromProblem.length > 0) return fromProblem;

  return response.headers.get("x-correlation-id") ?? fallback;
}

/* --------------------------------------------------------------------- client */

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { traceId, header: traceparent } = newTraceparent();
  const timeout = AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;

  const headers: Record<string, string> = {
    accept: "application/json",
    traceparent,
    ...options.headers,
  };
  if (options.body !== undefined) headers["content-type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.searchParams), {
      method: options.method ?? "GET",
      headers,
      body:
        options.formData ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      credentials: "same-origin", // sessions are cookie-based (D7)
      signal,
    });
  } catch (cause) {
    const aborted = signal.aborted;
    const message = aborted ? "Request timed out" : "Could not reach the server";
    logger.warn(message, {
      trace_id: traceId,
      path,
      cause: cause instanceof Error ? cause.message : String(cause),
    });
    throw new ApiError(message, 0, {
      traceId,
      correlationId: traceId,
      offline: !aborted,
      aborted,
    });
  }

  if (!response.ok) {
    const problem = await readProblem(response);
    throw new ApiError(
      problem?.title ?? response.statusText ?? "Request failed",
      response.status,
      {
        traceId,
        correlationId: correlationIdFrom(response, problem, traceId),
        retryAfterSeconds: parseRetryAfter(response.headers.get("retry-after")),
        offline: false,
        aborted: false,
      },
      problem,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T>(path: string, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  // PUT, not another PATCH: the two config endpoints replace a whole document,
  // and the verb is what tells a caller that anything it leaves out is deleted.
  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "PUT", body }),
  delete: <T>(path: string, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "DELETE" }),
};
