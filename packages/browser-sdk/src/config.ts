import { SITE_KEY_PATTERN } from "./constants";
import { reportOnce } from "./diagnostics";

/** Everything the SDK will accept. Only siteKey and endpoint are required. */
export interface NexovueOptions {
  /** The public site key: nvp_live_ plus 22 base62 characters. */
  siteKey: string;
  /** Origin of the nexovue receiver, e.g. https://ingest.nexovue.com. */
  endpoint: string;
  /** Names the property in the console. Defaults to the page's hostname. */
  serviceName?: string;
  /** Joins telemetry to a release. */
  serviceVersion?: string;
  /**
   * The route template this page belongs to, e.g. "/products/{id}". It is the
   * only page attribute that reaches an indexed column, and the SDK will never
   * guess one from the pathname.
   */
  route?: string;
  /**
   * Lets everything after the path leave the page: the query string and the
   * fragment, on url.full and as url.query.
   *
   * Off by default, and it is one switch rather than two because they are one
   * exposure. Masking in the pipeline matches attribute *keys*, not values, so a
   * token in a query string is stored verbatim for the whole retention period —
   * and a fragment is where an OAuth implicit flow puts an access token. The
   * default was settled by a page-view URL that carried a postal address while
   * this was off.
   */
  captureQueryAndFragment?: boolean;
  sessionIdleTimeoutMs?: number;
  sessionMaxDurationMs?: number;
  flushIntervalMs?: number;
}

export interface ResolvedConfig {
  siteKey: string;
  endpoint: string;
  serviceName: string;
  serviceVersion: string | undefined;
  route: string | undefined;
  captureQueryAndFragment: boolean;
  sessionIdleTimeoutMs: number;
  sessionMaxDurationMs: number;
  flushIntervalMs: number;
}

export interface Defaults {
  sessionIdleTimeoutMs: number;
  sessionMaxDurationMs: number;
  flushIntervalMs: number;
}

const SECRET_KEY_SCHEME = "nvk_live_";

/**
 * Resolves options into a configuration, or refuses with a message the customer
 * can act on. Refusing is the whole point of the checks: a malformed key
 * produces a 401 the door deliberately will not explain, and a secret key in a
 * page is a leaked credential rather than a typo.
 */
export function resolveConfig(
  options: NexovueOptions,
  defaults: Defaults,
): ResolvedConfig | undefined {
  if (typeof options !== "object" || options === null) {
    reportOnce("options", "refusing to start: init() needs an options object.");
    return undefined;
  }
  const siteKey = trimmed(options.siteKey) ?? "";
  if (siteKey.startsWith(SECRET_KEY_SCHEME)) {
    reportOnce(
      "secret-key",
      "refusing to start: that is a SECRET ingest key. A web page must use the " +
        "public site key (nvp_live_...). Rotate the secret key you pasted — it is " +
        "now in your page source.",
    );
    return undefined;
  }
  if (!SITE_KEY_PATTERN.test(siteKey)) {
    reportOnce(
      "site-key",
      "refusing to start: siteKey must be a public site key of the form " +
        "nvp_live_ followed by 22 letters or digits.",
    );
    return undefined;
  }

  const endpoint = normalizeEndpoint(options.endpoint);
  if (endpoint === undefined) {
    reportOnce(
      "endpoint",
      "refusing to start: endpoint must be an absolute http or https URL, " +
        "e.g. https://ingest.nexovue.com",
    );
    return undefined;
  }

  return {
    siteKey,
    endpoint,
    serviceName: trimmed(options.serviceName) ?? defaultServiceName(),
    serviceVersion: trimmed(options.serviceVersion),
    route: trimmed(options.route),
    captureQueryAndFragment: options.captureQueryAndFragment === true,
    sessionIdleTimeoutMs: positiveOr(options.sessionIdleTimeoutMs, defaults.sessionIdleTimeoutMs),
    sessionMaxDurationMs: positiveOr(options.sessionMaxDurationMs, defaults.sessionMaxDurationMs),
    flushIntervalMs: positiveOr(options.flushIntervalMs, defaults.flushIntervalMs),
  };
}

/**
 * A configuration value that is not a non-empty string is absent, not a crash.
 * The options object comes from a customer's page, where a number or a null is a
 * typo we would otherwise turn into a silent no-telemetry start.
 */
function trimmed(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.trim();
  return text === "" ? undefined : text;
}

/** Trims to an origin plus any path prefix, without the trailing slash the paths supply. */
function normalizeEndpoint(raw: string): string | undefined {
  const text = trimmed(raw);
  if (text === undefined) return undefined;
  let parsed: URL;
  try {
    parsed = new URL(text);
  } catch {
    return undefined;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return undefined;
  return `${parsed.origin}${parsed.pathname}`.replace(/\/+$/, "");
}

/** The site's own host names the property, so service.name is never empty. */
function defaultServiceName(): string {
  return globalThis.location?.hostname || "browser";
}

function positiveOr(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}
