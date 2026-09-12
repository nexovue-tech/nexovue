import { describe, expect, it } from "vitest";

import type { EndpointGroup, EndpointHealth, ServiceHealth } from "./api";
import {
  applyOrder,
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

describe("applyOrder", () => {
  const checkout = service("checkout", { p95_ms: 62.9 });
  const catalog = service("catalog", { p95_ms: 410 });
  const report = service("report", { p95_ms: 180 });

  it("leaves the API's ranking alone — worst first is the product, not a default", () => {
    const ranked = [checkout, catalog, report];
    expect(applyOrder(ranked, "worst")).toBe(ranked);
  });

  it("re-ranks by the slow tail when asked, without touching the input", () => {
    const ranked = [checkout, catalog, report];
    expect(applyOrder(ranked, "slowest").map((row) => row.service_name)).toEqual([
      "catalog",
      "report",
      "checkout",
    ]);
    expect(ranked[0]).toBe(checkout);
  });

  it("keeps the API's order between equal p95s rather than inventing one", () => {
    const first = service("first", { p95_ms: 100 });
    const second = service("second", { p95_ms: 100 });
    expect(applyOrder([first, second], "slowest").map((row) => row.service_name)).toEqual([
      "first",
      "second",
    ]);
  });
});

describe("buildServiceRows", () => {
  it("is one row per service while nothing is open", () => {
    const rows = buildServiceRows(
      [service("storefront"), service("catalog")],
      "worst",
      NOTHING_EXPANDED,
      NO_ENDPOINTS,
    );
    expect(rows.map((row) => row.kind)).toEqual(["service", "service"]);
    expect(rows[0].key).toBe(serviceRowKey("storefront"));
  });

  it("puts an open service's endpoints directly under it", () => {
    const rows = buildServiceRows(
      [service("storefront"), service("catalog")],
      "worst",
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

  it("ranks the endpoints the same way as the services", () => {
    const rows = buildServiceRows(
      [service("storefront")],
      "slowest",
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
    ).toEqual(["GET /report", "POST /checkout"]);
  });

  it("says it is loading in the render before the query exists", () => {
    const rows = buildServiceRows(
      [service("storefront")],
      "worst",
      new Set(["storefront"]),
      NO_ENDPOINTS,
    );
    expect(rows[1]).toMatchObject({ kind: "note", note: "pending" });
  });

  it("distinguishes a failed endpoint read from a silent service", () => {
    const failed = buildServiceRows(
      [service("storefront")],
      "worst",
      new Set(["storefront"]),
      new Map([["storefront", { status: "error", endpoints: [], refetch: () => {} }]]),
    );
    expect(failed[1]).toMatchObject({ kind: "note", note: "failed" });

    const silent = buildServiceRows(
      [service("storefront")],
      "worst",
      new Set(["storefront"]),
      new Map([["storefront", loaded([])]]),
    );
    expect(silent[1]).toMatchObject({ kind: "note", note: "empty" });
  });

  it("keys endpoints by kind as well as name — SERVER and CONSUMER are not one endpoint", () => {
    const rows = buildServiceRows(
      [service("worker")],
      "worst",
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
    const rows = buildServiceRows(
      [service("storefront")],
      "worst",
      new Set(["storefront"]),
      NO_ENDPOINTS,
    );
    expect(rowHeight(rows[1])).toBeGreaterThan(rowHeight(rows[0]));
  });
});

describe("describeServices", () => {
  it("names the count, the window and the ranking in force", () => {
    expect(describeServices(10, "hour", "worst")).toBe(
      "10 services reporting in the last hour, worst first.",
    );
    expect(describeServices(1, "24 hours", "slowest")).toBe(
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

  it("keeps the server's order under `worst`, however tempting the rate looks", () => {
    const asServed = [CHECKOUT, NOTIFICATIONS];

    expect(applyOrder(asServed, "worst").map((row) => row.service_name)).toEqual([
      "checkout-api",
      "notification-worker",
    ]);
  });

  it("does not resort by error rate under any order", () => {
    // `slowest` is the one escape hatch, and it sorts on the tail — never on error rate.
    const asServed = [
      service("fast-but-failing", { p95_ms: 10, error_rate: 0.9, error_count: 90 }),
      service("slow-and-fine", { p95_ms: 900, error_rate: 0, error_count: 0 }),
    ];

    expect(applyOrder(asServed, "slowest").map((row) => row.service_name)).toEqual([
      "slow-and-fine",
      "fast-but-failing",
    ]);
  });
});
