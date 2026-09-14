import { describe, expect, it } from "vitest";

import type { EndpointGroup, EndpointHealth, ServiceHealth } from "./api";
import {
  buildServiceRows,
  describeServices,
  describeVolume,
  orderCaption,
  rowHeight,
  serviceRowKey,
} from "./service-rows";

function service(name: string, overrides: Partial<ServiceHealth> = {}): ServiceHealth {
  return {
    service_name: name,
    request_count: 100,
    error_count: 0,
    requests_per_minute: 1.7,
    error_rate: 0,
    p50_ms: 10,
    p95_ms: 20,
    p99_ms: 30,
    ...overrides,
  };
}

function endpoint(
  serviceName: string,
  name: string,
  overrides: Partial<EndpointHealth> = {},
): EndpointHealth {
  return {
    service_name: serviceName,
    span_kind: "SERVER",
    endpoint: name,
    request_count: 100,
    error_count: 0,
    requests_per_minute: 1.7,
    error_rate: 0,
    p50_ms: 10,
    p95_ms: 20,
    p99_ms: 30,
    ...overrides,
  };
}

function loaded(endpoints: EndpointHealth[]): EndpointGroup {
  return { status: "success", endpoints, refetch: () => {} };
}

const NOTHING_EXPANDED: ReadonlySet<string> = new Set();
const NO_ENDPOINTS: ReadonlyMap<string, EndpointGroup> = new Map();

describe("buildServiceRows", () => {
  it("is one row per service while nothing is open", () => {
    const rows = buildServiceRows(
      [service("storefront"), service("catalog")],
      NOTHING_EXPANDED,
      NO_ENDPOINTS,
    );
    expect(rows.map((row) => row.kind)).toEqual(["service", "service"]);
    expect(rows[0].key).toBe(serviceRowKey("storefront"));
  });

  it("puts an open service's endpoints directly under it", () => {
    const rows = buildServiceRows(
      [service("storefront"), service("catalog")],
      new Set(["storefront"]),
      new Map([
        [
          "storefront",
          loaded([endpoint("storefront", "POST /checkout"), endpoint("storefront", "GET /cart")]),
        ],
      ]),
    );

    expect(rows.map((row) => row.kind)).toEqual(["service", "endpoint", "endpoint", "service"]);
    expect(rows[1]).toMatchObject({ endpoint: { endpoint: "POST /checkout" } });
  });

  it("renders the endpoints in the order they were served", () => {
    // Endpoints are ranked by the same statement and the same ordering as the
    // services above them, so the two agree without this module comparing
    // anything. Re-sorting here would have been a second opinion, over a page
    // the server had already cut.
    const rows = buildServiceRows(
      [service("storefront")],
      new Set(["storefront"]),
      new Map([
        [
          "storefront",
          loaded([
            endpoint("storefront", "POST /checkout", { p95_ms: 62.9 }),
            endpoint("storefront", "GET /report", { p95_ms: 900 }),
          ]),
        ],
      ]),
    );

    expect(
      rows.filter((row) => row.kind === "endpoint").map((row) => row.endpoint.endpoint),
    ).toEqual(["POST /checkout", "GET /report"]);
  });

  it("says it is loading in the render before the query exists", () => {
    const rows = buildServiceRows([service("storefront")], new Set(["storefront"]), NO_ENDPOINTS);
    expect(rows[1]).toMatchObject({ kind: "note", note: "pending" });
  });

  it("distinguishes a failed endpoint read from a silent service", () => {
    const failed = buildServiceRows(
      [service("storefront")],
      new Set(["storefront"]),
      new Map([["storefront", { status: "error", endpoints: [], refetch: () => {} }]]),
    );
    expect(failed[1]).toMatchObject({ kind: "note", note: "failed" });

    const silent = buildServiceRows(
      [service("storefront")],
      new Set(["storefront"]),
      new Map([["storefront", loaded([])]]),
    );
    expect(silent[1]).toMatchObject({ kind: "note", note: "empty" });
  });

  it("keys endpoints by kind as well as name — SERVER and CONSUMER are not one endpoint", () => {
    const rows = buildServiceRows(
      [service("worker")],
      new Set(["worker"]),
      new Map([
        [
          "worker",
          loaded([
            endpoint("worker", "process", { span_kind: "SERVER" }),
            endpoint("worker", "process", { span_kind: "CONSUMER" }),
          ]),
        ],
      ]),
    );
    const keys = rows.map((row) => row.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe("rowHeight", () => {
  it("gives the loading note room for the two lines it draws", () => {
    const rows = buildServiceRows([service("storefront")], new Set(["storefront"]), NO_ENDPOINTS);
    expect(rowHeight(rows[1])).toBeGreaterThan(rowHeight(rows[0]));
  });
});

describe("describeServices", () => {
  it("names the count, the window and the ranking in force", () => {
    expect(describeServices(10, "the last hour", "worst")).toBe(
      "10 services reporting in the last hour, worst first.",
    );
    expect(describeServices(1, "the last 24 hours", "slowest")).toBe(
      "1 service reporting in the last 24 hours, slowest first.",
    );
  });
});

describe("orderCaption", () => {
  it("reads as the end of the sentence above the list", () => {
    expect(orderCaption("worst")).toBe("worst first");
    expect(orderCaption("slowest")).toBe("slowest first");
  });
});

describe("describeVolume", () => {
  it("names the volume the rate came from", () => {
    expect(describeVolume(2867)).toBe("2,867 requests in this window");
  });

  it("agrees with itself in the singular", () => {
    expect(describeVolume(1)).toBe("1 request in this window");
  });
});

describe("the API's ranking is the product", () => {
  /**
   * Ranked by absolute harm, not by rate. An endpoint failing 4.1% of 1,204 calls has
   * hurt fifty people; one failing 50% of 96 has hurt forty-eight, and it is the busy one
   * a reader has to see first. `queries.py` orders on `error_count DESC` for exactly this
   * reason, and the screen must not quietly reverse it by sorting on a rate.
   */
  const CHECKOUT = service("checkout-api", {
    request_count: 1204,
    error_count: 49,
    error_rate: 49 / 1204,
    requests_per_minute: 20,
  });
  const NOTIFICATIONS = service("notification-worker", {
    request_count: 96,
    error_count: 48,
    error_rate: 0.5,
    requests_per_minute: 1.6,
  });

  it("renders the rows in the order they arrived, whatever the numbers look like", () => {
    // The ranking is the server's, and this module must not have an opinion
    // about it: `notification-worker` has the higher error *rate* and the lower
    // error *count*, and harm is what the ranking leads with.
    const rows = buildServiceRows([CHECKOUT, NOTIFICATIONS], NOTHING_EXPANDED, NO_ENDPOINTS).filter(
      (row) => row.kind === "service",
    );

    expect(rows.map((row) => (row.kind === "service" ? row.service.service_name : ""))).toEqual([
      "checkout-api",
      "notification-worker",
    ]);
  });
});
