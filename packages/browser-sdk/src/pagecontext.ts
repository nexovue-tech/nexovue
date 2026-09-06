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
 * What every record says about where it happened. session.id is on all of them
 * because it is the only join between a visitor's vitals, errors and page views
 * — there is no session table and session.id is not a promoted column anywhere.
 */
export function addPageContext(attributes: AttributeList, sessionId: string): AttributeList {
  const url = globalThis.location;
  return attributes
    .string(ATTR_SESSION_ID, sessionId)
    .string(ATTR_URL_FULL, url?.href)
    .string(ATTR_URL_PATH, url?.pathname);
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
    [ATTR_URL_FULL]: url?.href,
    [ATTR_URL_PATH]: url?.pathname,
    [ATTR_URL_SCHEME]: url?.protocol.replace(/:$/, ""),
  };
  // Off unless asked for: masking matches attribute keys, not values, so a token
  // in a query string would be stored verbatim for the whole retention period.
  if (config.captureUrlQuery) attributes[ATTR_URL_QUERY] = url?.search.replace(/^\?/, "");
  // The only page attribute that reaches an indexed column, and never guessed:
  // a route template is the customer's knowledge, not something derived from a path.
  if (config.route) attributes[ATTR_HTTP_ROUTE] = config.route;
  return attributes;
}
