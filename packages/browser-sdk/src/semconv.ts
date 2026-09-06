/**
 * Every attribute name the SDK puts on the wire, spelled once.
 *
 * A misspelling is invisible from the outside: the door parses OTLP/JSON with
 * unknown fields discarded and answers 200 either way, so a wrong name is
 * silently dropped rather than refused. One spelling per name, covered by a
 * fixture test, is what makes that safe.
 *
 * The Stable ones are marked; the rest are Development status in the
 * OpenTelemetry registry, which means they can still change under us.
 */

/** Resource. */
export const ATTR_SERVICE_NAME = "service.name"; // Stable
export const ATTR_SERVICE_VERSION = "service.version"; // Stable
export const ATTR_TELEMETRY_SDK_NAME = "telemetry.sdk.name"; // Stable
export const ATTR_TELEMETRY_SDK_LANGUAGE = "telemetry.sdk.language"; // Stable
export const ATTR_TELEMETRY_SDK_VERSION = "telemetry.sdk.version"; // Stable
export const ATTR_USER_AGENT_ORIGINAL = "user_agent.original"; // Stable
export const ATTR_BROWSER_LANGUAGE = "browser.language";
export const ATTR_BROWSER_BRANDS = "browser.brands";
export const ATTR_BROWSER_PLATFORM = "browser.platform";
export const ATTR_BROWSER_MOBILE = "browser.mobile";

/** Location. */
export const ATTR_URL_FULL = "url.full"; // Stable
export const ATTR_URL_PATH = "url.path"; // Stable
export const ATTR_URL_SCHEME = "url.scheme"; // Stable
export const ATTR_URL_QUERY = "url.query"; // Stable
export const ATTR_HTTP_ROUTE = "http.route"; // Stable

/** Session. */
export const ATTR_SESSION_ID = "session.id";

/**
 * Web vitals. The vital's name is a lowercase VALUE of browser.web_vital.name —
 * cls, fcp, inp, lcp, ttfb — never part of the key.
 */
export const ATTR_WEB_VITAL_NAME = "browser.web_vital.name";
export const ATTR_WEB_VITAL_VALUE = "browser.web_vital.value";
export const ATTR_WEB_VITAL_DELTA = "browser.web_vital.delta";
export const ATTR_WEB_VITAL_ID = "browser.web_vital.id";
export const ATTR_WEB_VITAL_RATING = "browser.web_vital.rating";
export const ATTR_WEB_VITAL_NAVIGATION_TYPE = "browser.web_vital.navigation_type";

/**
 * Exceptions. type and message are the two promoted to ClickHouse columns;
 * the stacktrace is deliberately not promoted and is read out of the attributes
 * JSON, which is where query-time symbolication reaches it.
 */
export const ATTR_EXCEPTION_TYPE = "exception.type"; // Stable
export const ATTR_EXCEPTION_MESSAGE = "exception.message"; // Stable
export const ATTR_EXCEPTION_STACKTRACE = "exception.stacktrace"; // Stable

/** Where an ErrorEvent said the throw happened, when there was no Error object. */
export const ATTR_CODE_FILE_PATH = "code.file.path"; // Stable
export const ATTR_CODE_LINE_NUMBER = "code.line.number"; // Stable
export const ATTR_CODE_COLUMN_NUMBER = "code.column.number"; // Stable
