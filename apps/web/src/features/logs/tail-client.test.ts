import { describe, expect, it } from "vitest";

import { NO_FILTERS } from "./api";
import { parseTailMessage, retryDelayMs, tailUrl } from "./tail-client";

const ORIGIN = "https://app.nexovue.test";
const PROJECT = "01a041fc-ba34-7562-bbeb-8e447919767d";

describe("tailUrl", () => {
  it("upgrades the page's scheme rather than hard-coding one", () => {
    expect(tailUrl(ORIGIN, PROJECT, NO_FILTERS)).toMatch(/^wss:/);
    expect(tailUrl("http://localhost:15173", PROJECT, NO_FILTERS)).toMatch(/^ws:/);
  });

  it("names the project and nothing else when there are no filters", () => {
    const url = new URL(tailUrl(ORIGIN, PROJECT, NO_FILTERS));
    expect([...url.searchParams.keys()]).toEqual(["project_id"]);
    expect(url.pathname).toBe("/api/v1/logs/tail");
  });

  it("carries the same filters the search is running", () => {
    const url = new URL(
      tailUrl(ORIGIN, PROJECT, {
        query: "declined",
        serviceName: "demo-payments",
        minSeverity: 17,
      }),
    );
    expect(url.searchParams.get("q")).toBe("declined");
    expect(url.searchParams.get("service")).toBe("demo-payments");
    expect(url.searchParams.get("min_severity")).toBe("17");
  });

  it("sends no text filter for a box holding only whitespace", () => {
    const url = new URL(tailUrl(ORIGIN, PROJECT, { query: "   " }));
    expect(url.searchParams.has("q")).toBe(false);
  });

  it("never names a tenant, whatever it is given", () => {
    // The server takes the tenant from the session cookie and reads it from nowhere
    // else; this is the client half of the same rule.
    const url = new URL(tailUrl(ORIGIN, PROJECT, { query: "tenant_id=org-b" }));
    expect(url.searchParams.has("tenant_id")).toBe(false);
    expect(url.searchParams.get("q")).toBe("tenant_id=org-b");
  });
});

describe("parseTailMessage", () => {
  it("reads the ready frame", () => {
    const message = parseTailMessage(
      JSON.stringify({ type: "ready", project_id: PROJECT, buffer_capacity: 2000 }),
    );
    expect(message).toEqual({ type: "ready", project_id: PROJECT, buffer_capacity: 2000 });
  });

  it("reads a records frame and the count it dropped", () => {
    const message = parseTailMessage(JSON.stringify({ type: "records", records: [], dropped: 12 }));
    expect(message).toEqual({ type: "records", records: [], dropped: 12 });
  });

  it("treats a records frame with no count as having dropped nothing", () => {
    const message = parseTailMessage(JSON.stringify({ type: "records", records: [] }));
    expect(message).toEqual({ type: "records", records: [], dropped: 0 });
  });

  it("reads a failure and its reason", () => {
    const message = parseTailMessage(JSON.stringify({ type: "failed", detail: "storage is gone" }));
    expect(message).toEqual({ type: "failed", detail: "storage is gone" });
  });

  it.each([
    ["not json at all", "{oops"],
    ["a bare string", '"pause"'],
    ["null", "null"],
    ["a type we do not serve", JSON.stringify({ type: "subscribe" })],
    ["a ready frame with no project", JSON.stringify({ type: "ready" })],
    ["a records frame with no records", JSON.stringify({ type: "records", dropped: 3 })],
  ])("ignores %s rather than ending the tail", (_name, raw) => {
    expect(parseTailMessage(raw)).toBeUndefined();
  });
});

describe("retryDelayMs", () => {
  it("backs off, and stops backing off", () => {
    expect(retryDelayMs(1)).toBe(1_000);
    expect(retryDelayMs(2)).toBe(2_000);
    expect(retryDelayMs(3)).toBe(4_000);
    expect(retryDelayMs(50)).toBe(15_000);
  });

  it("never waits less than the first delay", () => {
    expect(retryDelayMs(0)).toBe(1_000);
  });
});
