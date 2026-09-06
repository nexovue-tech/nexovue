// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetDiagnostics } from "./diagnostics";
import { beaconExport, endpointsFor, keepaliveExport, postExport } from "./transport";

const SITE_KEY = "nvp_live_dZjiAYryiKTJ45KgB1EFUH";
const ENDPOINTS = endpointsFor("https://ingest.test", "/browser/v1/logs", SITE_KEY);

function answer(status: number, body: unknown, headers: Record<string, string> = {}) {
  const payload = typeof body === "string" ? body : JSON.stringify(body);
  const contentType = typeof body === "string" ? "application/x-protobuf" : "application/json";
  return new Response(payload, { status, headers: { "content-type": contentType, ...headers } });
}

function saidToConsole(): string {
  return vi.mocked(console.error).mock.calls.map(String).join(" ");
}

beforeEach(() => {
  resetDiagnostics();
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("endpoints", () => {
  it("builds one url per signal from the configured base", () => {
    expect(ENDPOINTS.fetchUrl).toBe("https://ingest.test/browser/v1/logs");
  });

  it("puts the key in the query string only on the beacon url", () => {
    expect(ENDPOINTS.beaconUrl).toBe(
      `https://ingest.test/browser/v1/logs?nexovue_site_key=${SITE_KEY}`,
    );
    expect(ENDPOINTS.fetchUrl).not.toContain(SITE_KEY);
  });
});

describe("the fetch path", () => {
  it("sends the key in the header, the content type explicitly, and no credentials", async () => {
    const fetchMock = vi.fn(async () => answer(200, {}));
    vi.stubGlobal("fetch", fetchMock);

    await postExport(ENDPOINTS, SITE_KEY, "{}");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://ingest.test/browser/v1/logs");
    expect(init.headers).toEqual({
      "content-type": "application/json",
      "x-nexovue-key": SITE_KEY,
    });
    expect(init.credentials).toBe("omit");
    expect(init.mode).toBe("cors");
    expect(init.keepalive).toBe(false);
  });

  it("sends only headers the door's CORS allowlist names", async () => {
    const fetchMock = vi.fn(async () => answer(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    await postExport(ENDPOINTS, SITE_KEY, "{}");

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const allowed = new Set(["content-type", "content-encoding", "accept", "x-nexovue-key"]);
    for (const name of Object.keys(init.headers as Record<string, string>)) {
      expect(allowed.has(name.toLowerCase())).toBe(true);
    }
  });
});

describe("verdicts", () => {
  async function verdictFor(response: Response) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response),
    );
    return await postExport(ENDPOINTS, SITE_KEY, "{}");
  }

  it("accepts any 2xx and asks for nothing back", async () => {
    expect(await verdictFor(answer(200, {}))).toEqual({
      outcome: "accepted",
      retryAfterMs: undefined,
    });
  });

  it("retries a 429 that names a Retry-After, in the seconds it named", async () => {
    const refusal = { code: 8, message: "ingest rate limit exceeded for this key" };
    const result = await verdictFor(answer(429, refusal, { "retry-after": "3" }));
    expect(result).toEqual({ outcome: "retry", retryAfterMs: 3000 });
  });

  it("stops for the page on a 429 with no Retry-After, which is the monthly allowance", async () => {
    const message =
      "the included monthly browser ingest volume for this plan is spent; " +
      "ingestion resumes on 2026-10-01";
    expect(await verdictFor(answer(429, { code: 8, message }))).toMatchObject({ outcome: "stop" });
    expect(saidToConsole()).toContain(message);
  });

  it("stops on a 403 and repeats the door's wording, which is the customer's diagnosis", async () => {
    const message = "this ingest key is not allowed from this origin";
    expect(await verdictFor(answer(403, { code: 7, message }))).toMatchObject({ outcome: "stop" });
    expect(saidToConsole()).toContain(message);
  });

  it("stops on a 401, because a bad key will not fix itself", async () => {
    const refusal = { code: 16, message: "invalid or missing x-nexovue-key credential" };
    expect(await verdictFor(answer(401, refusal))).toMatchObject({ outcome: "stop" });
  });

  it("retries a 503", async () => {
    const refusal = { code: 14, message: "ingestion buffer unavailable, retry" };
    const result = await verdictFor(answer(503, refusal, { "retry-after": "5" }));
    expect(result).toEqual({ outcome: "retry", retryAfterMs: 5000 });
  });

  it("drops a 400, because resending the same body fails identically", async () => {
    const refusal = { code: 3, message: "request is not a valid ExportLogsServiceRequest" };
    expect(await verdictFor(answer(400, refusal))).toMatchObject({ outcome: "drop" });
  });

  it("drops a 413 rather than splitting a body the door already refused", async () => {
    const refusal = { code: 3, message: "request body is larger than the configured limit" };
    expect(await verdictFor(answer(413, refusal))).toMatchObject({ outcome: "drop" });
  });

  it("survives the 415 that is answered in protobuf rather than JSON", async () => {
    const result = await verdictFor(answer(415, "unsupported content type"));
    expect(result).toMatchObject({ outcome: "drop" });
    expect(saidToConsole()).toContain("HTTP 415");
  });

  it("retries a network error, which is all an ad blocker or a CSP looks like", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    expect(await postExport(ENDPOINTS, SITE_KEY, "{}")).toEqual({
      outcome: "retry",
      retryAfterMs: undefined,
    });
  });
});

describe("the exit path", () => {
  it("hands sendBeacon a raw string, which the browser sends as text/plain", () => {
    const sendBeacon = vi.fn(() => true);
    vi.stubGlobal("navigator", { sendBeacon });

    expect(beaconExport(ENDPOINTS, '{"resourceLogs":[]}')).toBe(true);
    expect(sendBeacon).toHaveBeenCalledWith(ENDPOINTS.beaconUrl, '{"resourceLogs":[]}');
  });

  it("reports a refused beacon rather than losing the batch silently", () => {
    vi.stubGlobal("navigator", { sendBeacon: vi.fn(() => false) });
    expect(beaconExport(ENDPOINTS, "{}")).toBe(false);
  });

  it("reports false where sendBeacon does not exist at all", () => {
    vi.stubGlobal("navigator", {});
    expect(beaconExport(ENDPOINTS, "{}")).toBe(false);
  });

  it("falls back to a keepalive fetch that stays safelisted: no headers, key in the url", () => {
    const fetchMock = vi.fn(async () => answer(200, {}));
    vi.stubGlobal("fetch", fetchMock);

    keepaliveExport(ENDPOINTS, "{}");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(ENDPOINTS.beaconUrl);
    expect(init.headers).toBeUndefined();
    expect(init.keepalive).toBe(true);
  });
});
