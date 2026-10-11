import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";

import { CLIENT_LOGS_PATH, createBeaconSink, MAX_BATCH } from "./log-beacon";
import type { LogRecord } from "./logger";

function record(overrides: Partial<LogRecord> = {}): LogRecord {
  return {
    timestamp: "2026-10-11T12:00:00.000Z",
    severity: "error",
    message: "Uncaught render error",
    service: "nexovue-web",
    ...overrides,
  };
}

type Send = (url: string, init: RequestInit) => Promise<unknown>;

function sentBodies(send: Mock<Send>) {
  return send.mock.calls.map(([, init]) => JSON.parse((init as RequestInit).body as string));
}

describe("the production log sink", () => {
  let send: Mock<Send>;
  let target: EventTarget;

  beforeEach(() => {
    vi.useFakeTimers();
    send = vi.fn<Send>().mockResolvedValue(new Response(null, { status: 204 }));
    target = new EventTarget();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends warnings and errors together after a short wait, to the API", async () => {
    const sink = createBeaconSink({ send, target, page: () => "/services" });

    sink(record());
    sink(record({ severity: "warn", message: "Resource failed to load" }));
    expect(send).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_000);

    expect(send).toHaveBeenCalledTimes(1);
    const [url, init] = send.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(CLIENT_LOGS_PATH);
    expect(init).toMatchObject({ method: "POST", keepalive: true, credentials: "same-origin" });
    const [body] = sentBodies(send);
    expect(body.records.map((r: { severity: string }) => r.severity)).toEqual(["error", "warn"]);
    expect(body.records[0].page).toBe("/services");
  });

  it("keeps debug and info in the browser", async () => {
    const sink = createBeaconSink({ send, target, page: () => "/" });

    sink(record({ severity: "debug" }));
    sink(record({ severity: "info" }));
    await vi.advanceTimersByTimeAsync(2_000);

    expect(send).not.toHaveBeenCalled();
  });

  it("sends what it holds when the page is being left", () => {
    const sink = createBeaconSink({ send, target, page: () => "/" });

    sink(record());
    target.dispatchEvent(new Event("pagehide"));

    expect(send).toHaveBeenCalledTimes(1);
  });

  it("never holds more than one batch, so a storm of errors cannot grow memory", async () => {
    const sink = createBeaconSink({ send, target, page: () => "/" });

    for (let i = 0; i < MAX_BATCH * 5; i++) sink(record({ message: `error ${i}` }));
    await vi.advanceTimersByTimeAsync(2_000);

    expect(sentBodies(send)[0].records).toHaveLength(MAX_BATCH);
  });

  it("swallows a failed send rather than logging it, which would loop", async () => {
    send.mockRejectedValue(new TypeError("Failed to fetch"));
    const sink = createBeaconSink({ send, target, page: () => "/" });

    sink(record());
    await expect(vi.advanceTimersByTimeAsync(2_000)).resolves.not.toThrow();
    expect(send).toHaveBeenCalledTimes(1);
  });
});
