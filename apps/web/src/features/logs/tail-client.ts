/**
 * The wire for the live tail: the URL, the two words we may say, and what we may hear.
 *
 * Framework-free and pure, so the protocol can be tested without a socket and without
 * React. The socket itself lives in `use-log-tail.ts`.
 *
 * This does not go through `shared/api-client.ts`, and that is not an exception to the
 * rule: the client wraps `fetch`, and a WebSocket is not a fetch. What the rule
 * protects — one place that knows the API path, and the session travelling as a cookie
 * — both hold here. `API_BASE_PATH` is imported rather than retyped, and the browser
 * attaches the session cookie to a same-origin handshake by itself.
 */

import { API_BASE_PATH } from "@/shared/api-client";
import type { LogSearchFilters } from "./api";
import type { LogLine } from "./log-feed";

export const TAIL_PATH = `${API_BASE_PATH}logs/tail`;

export const PAUSE_COMMAND = JSON.stringify({ action: "pause" });
export const RESUME_COMMAND = JSON.stringify({ action: "resume" });

export interface TailReady {
  type: "ready";
  project_id: string;
  buffer_capacity: number;
}

export interface TailRecords {
  type: "records";
  records: LogLine[];
  /** Lines the server discarded to make room since the previous frame. */
  dropped: number;
}

export interface TailFailed {
  type: "failed";
  detail: string;
}

export type TailMessage = TailReady | TailRecords | TailFailed;

/**
 * The tail's address, with the same filters the search is running.
 *
 * There is deliberately no tenant here. The server takes it from the session cookie and
 * refuses to read one from anything a caller sends — a subscription is a read, and a
 * read never names its own tenant.
 */
export function tailUrl(origin: string, projectId: string, filters: LogSearchFilters): string {
  const url = new URL(TAIL_PATH, origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("project_id", projectId);

  const query = filters.query.trim();
  if (query) url.searchParams.set("q", query);
  if (filters.serviceName) url.searchParams.set("service", filters.serviceName);
  if (filters.minSeverity) url.searchParams.set("min_severity", String(filters.minSeverity));

  return url.toString();
}

/**
 * Read one frame, or nothing.
 *
 * Total by design: a frame we cannot understand is skipped, never thrown. Ending
 * someone's tail because the server grew a field it does not yet know is a worse
 * failure than ignoring the field.
 */
export function parseTailMessage(raw: string): TailMessage | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return undefined;
  }
  if (typeof parsed !== "object" || parsed === null) return undefined;

  const message = parsed as Record<string, unknown>;
  switch (message.type) {
    case "ready":
      return typeof message.project_id === "string"
        ? {
            type: "ready",
            project_id: message.project_id,
            buffer_capacity:
              typeof message.buffer_capacity === "number" ? message.buffer_capacity : 0,
          }
        : undefined;
    case "records":
      return Array.isArray(message.records)
        ? {
            type: "records",
            records: message.records as LogLine[],
            dropped: typeof message.dropped === "number" ? message.dropped : 0,
          }
        : undefined;
    case "failed":
      return {
        type: "failed",
        detail: typeof message.detail === "string" ? message.detail : "The live tail ended.",
      };
    default:
      return undefined;
  }
}

/** Reconnect backoff: quick enough to be invisible, slow enough not to hammer. */
const FIRST_RETRY_MS = 1_000;
const MAX_RETRY_MS = 15_000;

export function retryDelayMs(consecutiveFailures: number): number {
  return Math.min(FIRST_RETRY_MS * 2 ** Math.max(0, consecutiveFailures - 1), MAX_RETRY_MS);
}
