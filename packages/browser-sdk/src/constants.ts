/**
 * Every tunable the SDK has, in one place. The receiver contract and the
 * browser's own ceilings are the source of most of them; where a number is a
 * convention we chose rather than a limit we were given, the comment says so.
 */

export const SDK_NAME = "@nexovue/browser-sdk";
export const SDK_VERSION = __NEXOVUE_SDK_VERSION__;

/** The three paths the browser door mounts, under its own prefix. */
export const BROWSER_PATH_PREFIX = "/browser";
export const TRACES_PATH = `${BROWSER_PATH_PREFIX}/v1/traces`;
export const LOGS_PATH = `${BROWSER_PATH_PREFIX}/v1/logs`;

/** The query parameter a beacon presents the key in; it cannot set headers. */
export const SITE_KEY_QUERY_PARAM = "nexovue_site_key";
/** The header a fetch presents the key in. Lower-case, as the door names it. */
export const SITE_KEY_HEADER = "x-nexovue-key";

/** A public site key: the scheme plus 22 base62 characters. */
export const SITE_KEY_PATTERN = /^nvp_live_[0-9A-Za-z]{22}$/;

/**
 * The semconv generation the attribute spellings below belong to. The receiver
 * stamps it on the envelope and the storage pipeline reads it to decide which
 * generation of HTTP attribute names is authoritative.
 */
export const SEMCONV_SCHEMA_URL = "https://opentelemetry.io/schemas/1.44.0";

/**
 * Half the browser's 64 KiB keepalive quota, which is cumulative across every
 * in-flight keepalive request the page has — including the customer's own — not
 * per request. The receiver's own limit is 4 MiB and never binds first.
 */
export const BATCH_MAX_BYTES = 32 * 1024;

/**
 * Flushing every 10s costs the site key one request per visitor per 10s against
 * a 1000/s per-key ceiling shared by every visitor, so this supports ~10,000
 * concurrent visitors before the site starts absorbing 429s.
 */
export const FLUSH_INTERVAL_MS = 10_000;

/** Retry schedule for a 503 or a network error. Doubles, jittered, capped. */
export const RETRY_BASE_DELAY_MS = 1_000;
export const RETRY_MAX_DELAY_MS = 30_000;
export const RETRY_MAX_ATTEMPTS = 4;

/**
 * A ceiling on what a paused or throttled queue may hold. Past it the oldest
 * records are dropped, because a page that cannot send must not grow without
 * bound in the customer's tab.
 */
export const PENDING_RECORDS_LIMIT = 500;

/**
 * Session lifetime. OpenTelemetry's session convention is Development status
 * and prescribes no timeout at all, so these are a convention we are choosing:
 * 30 minutes idle is GA4's default, and an absolute cap is Datadog's shape —
 * without one, a kiosk browser left open for a week is a single session.
 */
export const SESSION_IDLE_TIMEOUT_MS = 30 * 60 * 1000;
export const SESSION_MAX_DURATION_MS = 4 * 60 * 60 * 1000;

/** Namespaced per site key so two properties on one origin do not share a session. */
export const SESSION_STORAGE_KEY_PREFIX = "nexovue.session.";

/**
 * A fixed literal, never the URL. endpoint_stats_1m groups by span_name and
 * retains 13 months, so a per-URL name would mint a rollup row per URL per
 * minute.
 */
export const PAGE_VIEW_SPAN_NAME = "page_view";

/** OTLP log record event names. */
export const WEB_VITAL_EVENT_NAME = "browser.web_vital";
export const EXCEPTION_EVENT_NAME = "exception";

/** OTLP's normalised severity scale: INFO is 9, ERROR is 17. */
export const SEVERITY_INFO = 9;
export const SEVERITY_ERROR = 17;

/**
 * The type recorded when a throw carried no name — a rejected promise whose
 * reason was null, or an ErrorEvent with no Error object. The Errors screen
 * filters on `exception_type != ''`, so a record without one is invisible.
 */
export const FALLBACK_EXCEPTION_TYPE = "Error";

/** What the browser replaces a cross-origin script's error with. Some engines
 * append a period and some do not, so it is matched as a prefix. */
export const MUTED_ERROR_PREFIX = "Script error";
