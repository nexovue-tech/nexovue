import { SITE_KEY_HEADER, SITE_KEY_QUERY_PARAM } from "./constants";
import { reportOnce } from "./diagnostics";

/**
 * Two transports, and the split is not a preference.
 *
 * fetch is the primary one because it is the only one whose refusals a page can
 * read: the door echoes the caller's Origin on every refusal and exposes
 * Retry-After, so a wrong key or an origin the site key does not allow is
 * diagnosable from the very first export.
 *
 * navigator.sendBeacon is the unload transport, because it is the only request
 * that survives a page going away. It reports nothing but whether it was queued,
 * it can set no headers — hence the key in the query string — and its body must
 * be text/plain, which the door reads as OTLP/JSON: a beacon fixes its
 * credentials mode to "include", so any other content type is preflighted and
 * the browser then fails the POST against a door that never sends
 * Access-Control-Allow-Credentials.
 */

export type Outcome =
  /** The batch is in. Discard it. */
  | "accepted"
  /** Transient. Keep the batch and send it again after retryAfterMs. */
  | "retry"
  /** Permanent for this batch alone. Discard it. */
  | "drop"
  /** Permanent for the whole page. Stop sending. */
  | "stop";

export interface SendResult {
  outcome: Outcome;
  retryAfterMs: number | undefined;
}

export interface Endpoints {
  fetchUrl: string;
  beaconUrl: string;
}

/** One base, one hard-coded suffix per signal: a URL typo reaches the page as an
 * opaque CORS failure with no status, because a 404 under /browser is served
 * outside the CORS wrapper and carries no Access-Control-Allow-Origin. */
export function endpointsFor(endpoint: string, signalPath: string, siteKey: string): Endpoints {
  const fetchUrl = endpoint + signalPath;
  const query = `${SITE_KEY_QUERY_PARAM}=${encodeURIComponent(siteKey)}`;
  return { fetchUrl, beaconUrl: `${fetchUrl}?${query}` };
}

export async function postExport(
  endpoints: Endpoints,
  siteKey: string,
  body: string,
): Promise<SendResult> {
  let response: Response;
  try {
    response = await fetch(endpoints.fetchUrl, {
      method: "POST",
      // Content-Type must be explicit: an absent header selects the protobuf
      // decoder and every JSON body then fails to parse.
      headers: { "content-type": "application/json", [SITE_KEY_HEADER]: siteKey },
      body,
      mode: "cors",
      // The door never sends Access-Control-Allow-Credentials, so "include" fails.
      credentials: "omit",
      keepalive: false,
    });
  } catch {
    // A network error is indistinguishable from an ad blocker or a Content-Security-Policy
    // that omits our host from connect-src. Retrying is the only thing that separates them.
    return { outcome: "retry", retryAfterMs: undefined };
  }
  return await verdictFor(response);
}

/**
 * Returns false when the browser would not queue the beacon — over the 64 KiB
 * cumulative keepalive quota, most often. A false that nobody reads is silent
 * data loss.
 */
export function beaconExport(endpoints: Endpoints, body: string): boolean {
  if (typeof navigator.sendBeacon !== "function") return false;
  // A bare string is transmitted as text/plain;charset=UTF-8, which is what the door reads.
  return navigator.sendBeacon(endpoints.beaconUrl, body);
}

/**
 * The exit fallback when a beacon is refused. Same text/plain body and same
 * query-string key, so the request stays CORS-safelisted and needs no preflight
 * — there is no time for a round trip at unload. Its answer is unreadable in
 * practice and is not read.
 */
export function keepaliveExport(endpoints: Endpoints, body: string): void {
  void fetch(endpoints.beaconUrl, {
    method: "POST",
    body,
    mode: "cors",
    credentials: "omit",
    keepalive: true,
  }).catch(() => undefined);
}

async function verdictFor(response: Response): Promise<SendResult> {
  if (response.ok) return { outcome: "accepted", retryAfterMs: undefined };

  const retryAfterMs = retryAfterFrom(response);
  if (response.status === 429) {
    // Retry-After present is the per-second limiter and clears on its own.
    // Absent is the monthly browser allowance: retrying spends the customer's
    // egress for weeks to learn nothing.
    if (retryAfterMs !== undefined) return { outcome: "retry", retryAfterMs };
    reportOnce("allowance", await refusalMessage(response));
    return { outcome: "stop", retryAfterMs: undefined };
  }
  if (response.status >= 500) return { outcome: "retry", retryAfterMs };
  if (response.status === 401 || response.status === 403) {
    // The door writes these to be read by a human; they are the customer's whole diagnosis.
    reportOnce(`auth-${response.status}`, await refusalMessage(response));
    return { outcome: "stop", retryAfterMs: undefined };
  }
  reportOnce(`refused-${response.status}`, await refusalMessage(response));
  return { outcome: "drop", retryAfterMs: undefined };
}

function retryAfterFrom(response: Response): number | undefined {
  const header = response.headers.get("retry-after");
  if (!header) return undefined;
  const seconds = Number.parseInt(header, 10);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  const at = Date.parse(header);
  return Number.isNaN(at) ? undefined : Math.max(0, at - Date.now());
}

/**
 * Every refusal but one is a google.rpc.Status in the request's own codec. The
 * exception is an unsupported content type, answered in protobuf because the
 * codec was never resolved — so the body is a convenience, not a contract.
 */
async function refusalMessage(response: Response): Promise<string> {
  const fallback = `export refused with HTTP ${response.status}`;
  try {
    if (!response.headers.get("content-type")?.includes("json")) return fallback;
    const body: unknown = await response.json();
    const message = (body as { message?: unknown }).message;
    return typeof message === "string" && message !== "" ? message : fallback;
  } catch {
    return fallback;
  }
}
