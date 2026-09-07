import type { Attributes } from "@opentelemetry/api";

import type { ResolvedConfig } from "./config";
import type { AttributeList } from "./otlp/values";
import {
  ATTR_HTTP_ROUTE,
  ATTR_SESSION_ID,
  ATTR_URL_FULL,
  ATTR_URL_PATH,
  ATTR_URL_QUERY,
  ATTR_URL_SCHEME,
} from "./semconv";

/**
 * Query-string parameters whose values never leave the page, even when the page
 * asked for the query string. The conventions' own list, matched
 * case-sensitively as they specify, with the key kept so the shape of the URL
 * survives: `q=shoes&sig=REDACTED`.
 *
 * It is short because it is the presigned-URL list — AWS and Google signatures.
 * It is not what makes ADR 0103 safe; trimming by default is.
 *
 * Restated in `nexovue_scheduler.probes.urls` for the synthetic probe, and
 * asserted equal by `test_cross_service_contracts.py`, because TypeScript and
 * Python cannot import each other.
 */
export const SENSITIVE_QUERY_PARAMETERS = [
  "X-Amz-Credential",
  "X-Amz-Security-Token",
  "X-Amz-Signature",
  "X-Goog-Signature",
  "sig",
] as const;

const REDACTED = "REDACTED";

/**
 * The page's URL, as much of it as the page's owner allowed off the page.
 *
 * **Off — the default — is scheme, host and path.** ADR 0103: with the option
 * off, our own store held a page-view URL carrying an order and a postal
 * address, because `location.href` is the query string and the fragment whatever
 * a separate `url.query` option says. An OAuth fragment is the same exposure
 * holding a bearer token.
 *
 * Undefined where there is no location at all, which is a worker or a test
 * environment rather than a page.
 */
export function reportableUrl(config: ResolvedConfig): string | undefined {
  const href = globalThis.location?.href;
  if (href === undefined) return undefined;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return undefined;
  }
  // The conventions make this a MUST and a browser already honours it; cleared
  // anyway so the rule holds by construction rather than by the caller's luck.
  url.username = "";
  url.password = "";
  if (!config.captureQueryAndFragment) {
    url.search = "";
    url.hash = "";
    return url.href;
  }
  url.search = redactedQuery(url.search);
  return url.href;
}

/** The query string alone, redacted, without its leading `?`. Empty when off. */
export function reportableQuery(config: ResolvedConfig): string {
  if (!config.captureQueryAndFragment) return "";
  return redactedQuery(globalThis.location?.search ?? "").replace(/^\?/, "");
}

/**
 * Replaces the value of every sensitive parameter, and leaves the string
 * untouched when there is none — re-serialising a query string that needed no
 * change would rewrite its encoding for nothing.
 */
function redactedQuery(search: string): string {
  if (search === "") return "";
  const parameters = new URLSearchParams(search);
  let redacted = false;
  for (const name of SENSITIVE_QUERY_PARAMETERS) {
    if (!parameters.has(name)) continue;
    parameters.set(name, REDACTED);
    redacted = true;
  }
  return redacted ? `?${parameters.toString()}` : search;
}

/**
 * What every record says about where it happened. session.id is on all of them
 * because it is the only join between a visitor's vitals, errors and page views
 * — there is no session table and session.id is not a promoted column anywhere.
 *
 * http.route is here rather than on the page-view span alone because it is what
 * the store groups a page by: without it a vital lands under the raw path, and
 * a site with an order id in its URLs gets one breakdown group per order. It is
 * never guessed — a route template is the customer's knowledge, not something
 * derived from a path — so a page that configured none is grouped by its path.
 */
export function addPageContext(
  attributes: AttributeList,
  sessionId: string,
  config: ResolvedConfig,
): AttributeList {
  return attributes
    .string(ATTR_SESSION_ID, sessionId)
    .string(ATTR_URL_FULL, reportableUrl(config))
    .string(ATTR_URL_PATH, globalThis.location?.pathname)
    .string(ATTR_HTTP_ROUTE, config.route);
}

/**
 * The page-view span's attributes. A span carries them as the tracer's own
 * record rather than as an OTLP list, so this is the one place the two attribute
 * shapes meet.
 */
export function pageViewAttributes(config: ResolvedConfig, sessionId: string): Attributes {
  const url = globalThis.location;
  const attributes: Attributes = {
    [ATTR_SESSION_ID]: sessionId,
    [ATTR_URL_FULL]: reportableUrl(config),
    [ATTR_URL_PATH]: url?.pathname,
    [ATTR_URL_SCHEME]: url?.protocol.replace(/:$/, ""),
  };
  // One switch for everything after the path: url.full is trimmed to it and this
  // attribute is absent, so a token in a query string or an OAuth fragment
  // cannot leave the page by the other door. See ADR 0103.
  const query = reportableQuery(config);
  if (query !== "") attributes[ATTR_URL_QUERY] = query;
  // Spelled again rather than shared: a span carries the tracer's own attribute
  // shape, not an OTLP list. See addPageContext for why it is never guessed.
  if (config.route) attributes[ATTR_HTTP_ROUTE] = config.route;
  return attributes;
}
