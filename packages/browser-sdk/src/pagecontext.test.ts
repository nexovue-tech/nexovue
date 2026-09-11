// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import type { ResolvedConfig } from "./config";
import { pageViewAttributes, reportableUrl } from "./pagecontext";

/**
 * URL trimming, asserted rather than trusted.
 *
 * The defect this covers was in the store, not in a review: with the option off,
 * ten records from our own SDK had an empty `url.query` and two of them still
 * carried a 2.4 KB query string with a postal address in it, because `url.full`
 * was `location.href`. Every test below fails if that line comes back.
 */

const SESSION_ID = "0089dddd0089dddd0089dddd0089dddd";

function config(captureQueryAndFragment: boolean): ResolvedConfig {
  return {
    siteKey: "nvp_live_dZjiAYryiKTJ45KgB1EFUH",
    endpoint: "https://ingest.test",
    serviceName: "acme-storefront",
    serviceVersion: undefined,
    route: undefined,
    captureQueryAndFragment,
    sessionIdleTimeoutMs: 1800000,
    sessionMaxDurationMs: 14400000,
    flushIntervalMs: 10000,
  };
}

function visit(path: string): void {
  window.history.replaceState({}, "", path);
}

beforeEach(() => visit("/"));

describe("with the default configuration", () => {
  it("reports the path and nothing after it", () => {
    visit("/cart/checkout?order=%7B%22zipCode%22%3A%2294043%22%7D");
    expect(reportableUrl(config(false))).toBe("http://localhost:3000/cart/checkout");
  });

  it("drops the fragment, which is where an OAuth implicit flow puts a token", () => {
    visit("/callback#access_token=ya29.a0AfB_by&token_type=Bearer");
    expect(reportableUrl(config(false))).toBe("http://localhost:3000/callback");
  });

  it("sends no url.query attribute at all", () => {
    visit("/search?q=shoes");
    expect(Object.keys(pageViewAttributes(config(false), SESSION_ID))).not.toContain("url.query");
  });
});

describe("when the page asks for the query string and the fragment", () => {
  it("reports both", () => {
    visit("/search?q=shoes#results");
    expect(reportableUrl(config(true))).toBe("http://localhost:3000/search?q=shoes#results");
  });

  it("sends url.query without its leading question mark", () => {
    visit("/search?q=shoes");
    expect(pageViewAttributes(config(true), SESSION_ID)["url.query"]).toBe("q=shoes");
  });

  it("redacts the conventions' sensitive parameters and keeps their keys", () => {
    visit("/download?file=a.pdf&X-Amz-Signature=deadbeef&sig=abc123");
    const url = reportableUrl(config(true)) ?? "";
    expect(url).toContain("file=a.pdf");
    expect(url).toContain("X-Amz-Signature=REDACTED");
    expect(url).toContain("sig=REDACTED");
    expect(url).not.toContain("deadbeef");
    expect(url).not.toContain("abc123");
  });

  it("matches those parameter names case-sensitively, as the conventions specify", () => {
    visit("/download?SIG=abc123");
    expect(reportableUrl(config(true))).toContain("SIG=abc123");
  });

  it("leaves a query needing no change exactly as the page had it", () => {
    // Re-serialising would turn %20 into + and reorder nothing usefully, which
    // makes a customer's URL unrecognisable to them for no gain.
    visit("/search?q=running%20shoes&page=2");
    expect(reportableUrl(config(true))).toBe(
      "http://localhost:3000/search?q=running%20shoes&page=2",
    );
  });
});
