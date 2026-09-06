// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BATCH_MAX_BYTES, PENDING_RECORDS_LIMIT } from "./constants";
import { failureCounts, resetDiagnostics } from "./diagnostics";
import { createExporter } from "./exporter";
import type { LogRecordJson } from "./otlp/logrecords";
import type { SpanJson } from "./otlp/spans";
import { endpointsFor } from "./transport";

const SITE_KEY = "nvp_live_dZjiAYryiKTJ45KgB1EFUH";

function newExporter() {
  return createExporter({
    siteKey: SITE_KEY,
    resource: [{ key: "service.name", value: { stringValue: "acme-storefront" } }],
    logsEndpoints: endpointsFor("https://ingest.test", "/browser/v1/logs", SITE_KEY),
    tracesEndpoints: endpointsFor("https://ingest.test", "/browser/v1/traces", SITE_KEY),
  });
}

function logRecord(message = "x"): LogRecordJson {
  return {
    timeUnixNano: "1788626299167000000",
    observedTimeUnixNano: "1788626299167000000",
    severityNumber: 9,
    severityText: "INFO",
    eventName: "browser.web_vital",
    attributes: [{ key: "exception.message", value: { stringValue: message } }],
  };
}

function spanRecord(): SpanJson {
  return {
    traceId: "00c0ffee00c0ffee00c0ffee00c0ffee",
    spanId: "00c0ffee00c0ffee",
    name: "page_view",
    kind: 1,
    startTimeUnixNano: "1788626299000000000",
    endTimeUnixNano: "1788626300000000000",
    attributes: [],
    status: { code: 0 },
  };
}

function accepted() {
  return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
}

function refusedWith(status: number, message: string, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ code: 0, message }), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/** Lets the awaits inside a drain settle, including the response body read. */
async function settle() {
  for (let tick = 0; tick < 25; tick++) await Promise.resolve();
}

beforeEach(() => {
  resetDiagnostics();
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("batching", () => {
  it("sends every buffered record of a signal in one request", async () => {
    const fetchMock = vi.fn(async () => accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    exporter.enqueueLog(logRecord("first"));
    exporter.enqueueLog(logRecord("second"));
    exporter.enqueueLog(logRecord("third"));
    exporter.flush();
    await settle();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.resourceLogs[0].scopeLogs[0].logRecords).toHaveLength(3);
  });

  it("keeps logs and spans on their own signal paths", async () => {
    const fetchMock = vi.fn(async () => accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    exporter.enqueueLog(logRecord());
    exporter.enqueueSpan(spanRecord());
    exporter.flush();
    await settle();

    const urls = fetchMock.mock.calls.map((call) => String((call as unknown[])[0])).sort();
    expect(urls).toEqual([
      "https://ingest.test/browser/v1/logs",
      "https://ingest.test/browser/v1/traces",
    ]);
  });

  it("sends nothing when there is nothing buffered", async () => {
    const fetchMock = vi.fn(async () => accepted());
    vi.stubGlobal("fetch", fetchMock);

    newExporter().flush();
    await settle();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("flushes on its own once the batch reaches the byte cap", async () => {
    const fetchMock = vi.fn(async () => accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    const bulky = logRecord("y".repeat(4096));
    for (let count = 0; count < Math.ceil(BATCH_MAX_BYTES / 4096) + 1; count++) {
      exporter.enqueueLog(bulky);
    }
    await settle();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("refusals", () => {
  it("keeps a retried batch and sends it again once the pause elapses", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(1788626299167);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(refusedWith(429, "ingest rate limit exceeded", { "retry-after": "3" }))
      .mockResolvedValue(accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    exporter.enqueueLog(logRecord("kept"));
    exporter.flush();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Still inside the pause the door asked for.
    exporter.flush();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    vi.setSystemTime(1788626299167 + 3001);
    exporter.flush();
    await settle();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(init.body as string).toContain("kept");
  });

  it("stops sending for the whole page once the allowance is spent", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(refusedWith(429, "the included monthly browser ingest volume"))
      .mockResolvedValue(accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    exporter.enqueueLog(logRecord());
    exporter.flush();
    await settle();
    expect(exporter.hasStopped()).toBe(true);

    exporter.enqueueLog(logRecord());
    exporter.flush();
    exporter.flushOnExit();
    await settle();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("discards a dropped batch rather than resending a body the door refused", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(refusedWith(400, "request is not a valid ExportLogsServiceRequest"))
      .mockResolvedValue(accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    exporter.enqueueLog(logRecord("doomed"));
    exporter.flush();
    await settle();

    exporter.flush();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("the queue's own ceiling", () => {
  it("drops the oldest records rather than growing without bound in a paused tab", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(1788626299167);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(refusedWith(429, "rate limited", { "retry-after": "300" }))
      .mockResolvedValue(accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    exporter.enqueueLog(logRecord("first"));
    exporter.flush();
    await settle();

    // The signal is now paused for five minutes, so nothing drains it.
    for (let count = 0; count < PENDING_RECORDS_LIMIT * 2; count++) {
      exporter.enqueueLog(logRecord(`r${count}`));
    }
    expect(failureCounts()["exporter.overflow"]).toBeGreaterThan(0);

    vi.setSystemTime(1788626299167 + 300_001);
    exporter.flush();
    await settle();

    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    const kept = JSON.parse(init.body as string).resourceLogs[0].scopeLogs[0].logRecords;
    expect(kept).toHaveLength(PENDING_RECORDS_LIMIT);
  });
});

describe("the exit flush", () => {
  it("sends one beacon per signal that has data, logs first", () => {
    const sendBeacon = vi.fn(() => true);
    vi.stubGlobal("navigator", { sendBeacon });
    const exporter = newExporter();

    exporter.enqueueLog(logRecord());
    exporter.enqueueSpan(spanRecord());
    exporter.flushOnExit();

    expect(sendBeacon).toHaveBeenCalledTimes(2);
    const beaconed = sendBeacon.mock.calls.map((call) => String((call as unknown as string[])[0]));
    expect(beaconed[0]).toContain("/browser/v1/logs");
    expect(beaconed[1]).toContain("/browser/v1/traces");
  });

  it("sends nothing twice when both lifecycle events fire", () => {
    const sendBeacon = vi.fn(() => true);
    vi.stubGlobal("navigator", { sendBeacon });
    const exporter = newExporter();

    exporter.enqueueLog(logRecord());
    exporter.flushOnExit();
    exporter.flushOnExit();

    expect(sendBeacon).toHaveBeenCalledTimes(1);
  });

  it("falls back to a keepalive fetch when the beacon is refused", () => {
    vi.stubGlobal("navigator", { sendBeacon: vi.fn(() => false) });
    const fetchMock = vi.fn(async () => accepted());
    vi.stubGlobal("fetch", fetchMock);
    const exporter = newExporter();

    exporter.enqueueLog(logRecord());
    exporter.flushOnExit();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.keepalive).toBe(true);
  });
});
