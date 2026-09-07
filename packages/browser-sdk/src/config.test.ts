// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resolveConfig } from "./config";
import { resetDiagnostics } from "./diagnostics";

const DEFAULTS = {
  sessionIdleTimeoutMs: 1800000,
  sessionMaxDurationMs: 14400000,
  flushIntervalMs: 10000,
};
const VALID_KEY = "nvp_live_dZjiAYryiKTJ45KgB1EFUH";

let refusals: string[];

beforeEach(() => {
  resetDiagnostics();
  refusals = [];
  vi.spyOn(console, "error").mockImplementation((message: string) => {
    refusals.push(message);
  });
});

afterEach(() => vi.restoreAllMocks());

describe("site key", () => {
  it("accepts a public site key", () => {
    expect(
      resolveConfig({ siteKey: VALID_KEY, endpoint: "https://ingest.test" }, DEFAULTS),
    ).toBeDefined();
  });

  it("refuses a secret key and says the credential is now in the page", () => {
    const config = resolveConfig(
      { siteKey: "nvk_live_devlocaldevlocaldevloc", endpoint: "https://ingest.test" },
      DEFAULTS,
    );
    expect(config).toBeUndefined();
    expect(refusals.join(" ")).toContain("SECRET ingest key");
  });

  it.each([
    "nvp_live_short",
    "nvp_live_dZjiAYryiKTJ45KgB1EFU!",
    "",
    "nvp_test_dZjiAYryiKTJ45KgB1EFUH",
  ])("refuses %o rather than letting the door answer an unexplained 401", (siteKey) => {
    expect(resolveConfig({ siteKey, endpoint: "https://ingest.test" }, DEFAULTS)).toBeUndefined();
  });
});

describe("endpoint", () => {
  it("strips a trailing slash so the signal path concatenates cleanly", () => {
    const config = resolveConfig(
      { siteKey: VALID_KEY, endpoint: "https://ingest.test/" },
      DEFAULTS,
    );
    expect(config?.endpoint).toBe("https://ingest.test");
  });

  it("keeps a path prefix, which a first-party proxy needs", () => {
    const config = resolveConfig(
      { siteKey: VALID_KEY, endpoint: "https://shop.test/rum/" },
      DEFAULTS,
    );
    expect(config?.endpoint).toBe("https://shop.test/rum");
  });

  it.each(["", "ingest.test", "ws://ingest.test", "not a url"])("refuses %o", (endpoint) => {
    expect(resolveConfig({ siteKey: VALID_KEY, endpoint }, DEFAULTS)).toBeUndefined();
  });
});

describe("defaults", () => {
  it("names the service after the page's host, so service_name is never empty", () => {
    const config = resolveConfig({ siteKey: VALID_KEY, endpoint: "https://ingest.test" }, DEFAULTS);
    expect(config?.serviceName).toBe(window.location.hostname);
  });

  it("leaves the query string and the fragment off, both on one switch", () => {
    const config = resolveConfig({ siteKey: VALID_KEY, endpoint: "https://ingest.test" }, DEFAULTS);
    expect(config?.captureQueryAndFragment).toBe(false);
  });

  it("ignores a nonsensical interval rather than scheduling on it", () => {
    const config = resolveConfig(
      { siteKey: VALID_KEY, endpoint: "https://ingest.test", flushIntervalMs: -1 },
      DEFAULTS,
    );
    expect(config?.flushIntervalMs).toBe(DEFAULTS.flushIntervalMs);
  });
});

describe("options that are not strings", () => {
  it("treats a non-string serviceName as absent rather than failing to start", () => {
    const config = resolveConfig(
      { siteKey: VALID_KEY, endpoint: "https://ingest.test", serviceName: 42 } as never,
      DEFAULTS,
    );
    expect(config?.serviceName).toBe(window.location.hostname);
  });

  it("refuses an options object that is not an object", () => {
    expect(resolveConfig(null as never, DEFAULTS)).toBeUndefined();
    expect(refusals.join(" ")).toContain("options object");
  });
});
