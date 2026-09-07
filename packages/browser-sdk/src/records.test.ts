// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Metric } from "web-vitals";

import type { ResolvedConfig } from "./config";
import { logsExport } from "./otlp/logrecords";
import { errorRecord, webVitalRecord } from "./records";

/**
 * The wire shape, asserted key by key. The door parses OTLP/JSON with unknown
 * fields discarded, so a misspelled attribute is silently dropped and still
 * answers 200 — nothing but a test on this side would ever notice.
 */

const SESSION_ID = "0089dddd0089dddd0089dddd0089dddd";

/**
 * The default configuration, which is the one that matters here: the page below
 * has a query string and a fragment, and ADR 0103 says neither reaches a record
 * unless the page asked. `TRIMMED_URL` is what url.full must be instead.
 */
function config(captureQueryAndFragment = false, route?: string): ResolvedConfig {
  return {
    siteKey: "nvp_live_dZjiAYryiKTJ45KgB1EFUH",
    endpoint: "https://ingest.test",
    serviceName: "acme-storefront",
    serviceVersion: undefined,
    route,
    captureQueryAndFragment,
    sessionIdleTimeoutMs: 1800000,
    sessionMaxDurationMs: 14400000,
    flushIntervalMs: 10000,
  };
}

/** The template a customer configures, and the URL a browser is actually on. */
const ROUTE = "/cart/checkout/{orderId}";

const TRIMMED_URL = "http://localhost:3000/checkout";

beforeEach(() => {
  vi.spyOn(Date, "now").mockReturnValue(1788626299167);
  window.history.replaceState({}, "", "/checkout?coupon=SPRING#step2");
});

function lcp(): Metric {
  return {
    name: "LCP",
    value: 1842.5,
    delta: 1842.5,
    id: "v6-1788626299167-1234567890123",
    rating: "needs-improvement",
    navigationType: "navigate",
    navigationId: 1,
    entries: [],
  };
}

describe("a web vital", () => {
  it("is one log record carrying the vital's name as a lowercase value", () => {
    expect(webVitalRecord(lcp(), SESSION_ID, config())).toEqual({
      timeUnixNano: "1788626299167000000",
      observedTimeUnixNano: "1788626299167000000",
      severityNumber: 9,
      severityText: "INFO",
      eventName: "browser.web_vital",
      attributes: [
        { key: "browser.web_vital.name", value: { stringValue: "lcp" } },
        { key: "browser.web_vital.value", value: { doubleValue: 1842.5 } },
        { key: "browser.web_vital.delta", value: { doubleValue: 1842.5 } },
        { key: "browser.web_vital.id", value: { stringValue: "v6-1788626299167-1234567890123" } },
        { key: "browser.web_vital.rating", value: { stringValue: "needs-improvement" } },
        { key: "browser.web_vital.navigation_type", value: { stringValue: "navigate" } },
        { key: "session.id", value: { stringValue: SESSION_ID } },
        { key: "url.full", value: { stringValue: TRIMMED_URL } },
        { key: "url.path", value: { stringValue: "/checkout" } },
      ],
    });
  });

  it("never carries a flat browser.web_vital attribute, which would collide with the paths", () => {
    const keys = webVitalRecord(lcp(), SESSION_ID, config()).attributes.map((entry) => entry.key);
    expect(keys).not.toContain("browser.web_vital");
    expect(keys).not.toContain("browser.web_vital.lcp");
  });

  it("passes a soft navigation through rather than rewriting it to a known value", () => {
    const record = webVitalRecord(
      { ...lcp(), navigationType: "soft-navigation" },
      SESSION_ID,
      config(),
    );
    expect(record.attributes).toContainEqual({
      key: "browser.web_vital.navigation_type",
      value: { stringValue: "soft-navigation" },
    });
  });

  it("carries a zero CLS as a real reading rather than dropping it", () => {
    const record = webVitalRecord(
      { ...lcp(), name: "CLS", value: 0, delta: 0 },
      SESSION_ID,
      config(),
    );
    expect(record.attributes).toContainEqual({
      key: "browser.web_vital.value",
      value: { doubleValue: 0 },
    });
  });

  it("never serialises metric.entries", () => {
    const withEntries = { ...lcp(), entries: [{ name: "huge" } as PerformanceEntry] };
    expect(JSON.stringify(webVitalRecord(withEntries, SESSION_ID, config()))).not.toContain("huge");
  });
});

/**
 * The breakdown groups by `http.route` and falls back to `url.path`, so a vital
 * that reaches the store without the template lands under the raw URL. Our own
 * dev store held `/cart/checkout/978ac453-…`: one breakdown group per order.
 */
describe("the route template", () => {
  it("is on a vital record when the customer configured one", () => {
    const record = webVitalRecord(lcp(), SESSION_ID, config(false, ROUTE));
    expect(record.attributes).toContainEqual({
      key: "http.route",
      value: { stringValue: ROUTE },
    });
    // The raw path travels beside it; the store needs both to tell them apart.
    expect(record.attributes).toContainEqual({
      key: "url.path",
      value: { stringValue: "/checkout" },
    });
  });

  it("is on an error record too, from the same page context", () => {
    const record = errorRecord(
      {
        type: "TypeError",
        message: "t is not a function",
        stacktrace: "TypeError: t is not a function",
        filePath: undefined,
        line: undefined,
        column: undefined,
      },
      SESSION_ID,
      config(false, ROUTE),
    );
    expect(record.attributes).toContainEqual({
      key: "http.route",
      value: { stringValue: ROUTE },
    });
  });

  it("is absent rather than empty when the customer configured none", () => {
    const keys = webVitalRecord(lcp(), SESSION_ID, config()).attributes.map((entry) => entry.key);
    expect(keys).not.toContain("http.route");
  });

  it("is never derived from a path that looks like it has an id in it", () => {
    window.history.replaceState({}, "", "/cart/checkout/978ac453-aa9f-11f1-91c3-1e31e47ab720");
    const record = webVitalRecord(lcp(), SESSION_ID, config());
    const keys = record.attributes.map((entry) => entry.key);
    expect(keys).not.toContain("http.route");
    expect(record.attributes).toContainEqual({
      key: "url.path",
      value: { stringValue: "/cart/checkout/978ac453-aa9f-11f1-91c3-1e31e47ab720" },
    });
  });
});

describe("a JS error", () => {
  const captured = {
    type: "TypeError",
    message: "t is not a function",
    stacktrace: "TypeError: t is not a function\n    at n (https://cdn.test/a.min.js:1:2145)",
    filePath: undefined,
    line: undefined,
    column: undefined,
  };

  it("is one ERROR log record with the three exception attributes", () => {
    expect(errorRecord(captured, SESSION_ID, config())).toEqual({
      timeUnixNano: "1788626299167000000",
      observedTimeUnixNano: "1788626299167000000",
      severityNumber: 17,
      severityText: "ERROR",
      eventName: "exception",
      body: { stringValue: "t is not a function" },
      attributes: [
        { key: "exception.type", value: { stringValue: "TypeError" } },
        { key: "exception.message", value: { stringValue: "t is not a function" } },
        { key: "exception.stacktrace", value: { stringValue: captured.stacktrace } },
        { key: "session.id", value: { stringValue: SESSION_ID } },
        { key: "url.full", value: { stringValue: TRIMMED_URL } },
        { key: "url.path", value: { stringValue: "/checkout" } },
      ],
    });
  });

  it("carries no trace context, or the Errors screen would count a page's errors as one", () => {
    const record = errorRecord(captured, SESSION_ID, config()) as unknown as Record<
      string,
      unknown
    >;
    expect(record.traceId).toBeUndefined();
    expect(record.spanId).toBeUndefined();
  });

  it("records the event's own location when there was no Error object", () => {
    const record = errorRecord(
      {
        ...captured,
        stacktrace: undefined,
        filePath: "https://shop.test/app.js",
        line: 12,
        column: 5,
      },
      SESSION_ID,
      config(),
    );
    expect(record.attributes).toContainEqual({
      key: "code.line.number",
      value: { intValue: "12" },
    });
    expect(record.attributes.map((entry) => entry.key)).not.toContain("exception.stacktrace");
  });

  it("always has exception.type, which the Errors screen filters on", () => {
    const record = errorRecord({ ...captured, type: "Error", message: "" }, SESSION_ID, config());
    expect(record.attributes).toContainEqual({
      key: "exception.type",
      value: { stringValue: "Error" },
    });
  });
});

describe("the logs envelope", () => {
  it("names the scope and its schema url, and batches every record into one export", () => {
    const body = JSON.parse(
      logsExport(
        [{ key: "service.name", value: { stringValue: "acme-storefront" } }],
        [
          webVitalRecord(lcp(), SESSION_ID, config()),
          errorRecord(
            {
              ...{
                type: "Error",
                message: "x",
                stacktrace: undefined,
                filePath: undefined,
                line: undefined,
                column: undefined,
              },
            },
            SESSION_ID,
            config(),
          ),
        ],
      ),
    );
    const scope = body.resourceLogs[0].scopeLogs[0];
    expect(body.resourceLogs[0].resource.attributes[0].key).toBe("service.name");
    expect(scope.scope.name).toBe("@nexovue/browser-sdk");
    expect(scope.schemaUrl).toBe("https://opentelemetry.io/schemas/1.44.0");
    expect(scope.logRecords).toHaveLength(2);
  });
});
