// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetDiagnostics } from "./diagnostics";
import { init } from "./index";

const SITE_KEY = "nvp_live_dZjiAYryiKTJ45KgB1EFUH";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => [...values.keys()][index] ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  } as Storage;
}

let started: ReturnType<typeof init> | undefined;

beforeEach(() => {
  resetDiagnostics();
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 200 })),
  );
});

afterEach(() => {
  started?.shutdown();
  started = undefined;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("a refused configuration", () => {
  it("hands back an inert handle rather than undefined, so the page cannot get a TypeError", () => {
    const rum = init({ siteKey: "nope", endpoint: "https://ingest.test" });
    expect(rum.active).toBe(false);
    expect(() => rum.flush()).not.toThrow();
    expect(() => rum.shutdown()).not.toThrow();
    expect(rum.diagnostics()).toBeTypeOf("object");
  });

  it("never throws, whatever it is handed", () => {
    const nonsense = [
      undefined,
      null,
      {},
      { siteKey: 42, endpoint: [] },
      { siteKey: SITE_KEY },
      { endpoint: "https://ingest.test" },
    ];
    for (const options of nonsense) {
      expect(() => init(options as unknown as { siteKey: string; endpoint: string })).not.toThrow();
    }
  });
});

describe("a running SDK", () => {
  it("starts with only a site key and an endpoint", () => {
    started = init({ siteKey: SITE_KEY, endpoint: "https://ingest.test" });
    expect(started.active).toBe(true);
  });

  it("registers no unload or beforeunload listener anywhere, web-vitals included", () => {
    const onWindow = vi.spyOn(window, "addEventListener");
    const onDocument = vi.spyOn(document, "addEventListener");

    started = init({ siteKey: SITE_KEY, endpoint: "https://ingest.test" });

    const registered = [...onWindow.mock.calls, ...onDocument.mock.calls].map((call) =>
      String(call[0]),
    );
    expect(registered).not.toContain("unload");
    expect(registered).not.toContain("beforeunload");
  });

  it("does not replace the page's own window.onerror", () => {
    const pageHandler = vi.fn();
    window.onerror = pageHandler;

    started = init({ siteKey: SITE_KEY, endpoint: "https://ingest.test" });

    expect(window.onerror).toBe(pageHandler);
    window.onerror = null;
  });

  it("refuses a second init rather than doubling every record", () => {
    started = init({ siteKey: SITE_KEY, endpoint: "https://ingest.test" });
    const second = init({ siteKey: SITE_KEY, endpoint: "https://ingest.test" });

    expect(second).toBe(started);
    expect(vi.mocked(console.error).mock.calls.map(String).join(" ")).toContain("twice");
  });

  it("collects an error thrown by the page and sends it on flush", async () => {
    started = init({ siteKey: SITE_KEY, endpoint: "https://ingest.test" });

    const error = new TypeError("t is not a function");
    error.stack = "TypeError: t is not a function\n    at n (https://cdn.test/a.min.js:1:12)";
    window.dispatchEvent(new ErrorEvent("error", { message: "Uncaught TypeError", error }));

    started.flush();
    for (let tick = 0; tick < 25; tick++) await Promise.resolve();

    const fetchMock = vi.mocked(globalThis.fetch);
    const [url, request] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://ingest.test/browser/v1/logs");
    const record = JSON.parse(request.body as string).resourceLogs[0].scopeLogs[0].logRecords[0];
    expect(record.eventName).toBe("exception");
    expect(record.attributes).toContainEqual({
      key: "exception.stacktrace",
      value: { stringValue: error.stack },
    });
    expect(record.attributes.some((entry: { key: string }) => entry.key === "session.id")).toBe(
      true,
    );
  });

  it("stops collecting after shutdown", async () => {
    started = init({ siteKey: SITE_KEY, endpoint: "https://ingest.test" });
    started.shutdown();
    const rum = started;
    started = undefined;

    window.dispatchEvent(new ErrorEvent("error", { message: "after shutdown", error: null }));
    rum.flush();
    for (let tick = 0; tick < 25; tick++) await Promise.resolve();

    const paths = vi.mocked(globalThis.fetch).mock.calls.map((call) => String(call[0]));
    expect(paths).not.toContain("https://ingest.test/browser/v1/logs");
  });
});
