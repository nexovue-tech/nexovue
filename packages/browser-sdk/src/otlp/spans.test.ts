import type { ReadableSpan } from "@opentelemetry/sdk-trace-web";
import { describe, expect, it } from "vitest";

import { toSpanJson, tracesExport } from "./spans";

/**
 * The defect the kind test guards: the JavaScript API numbers SpanKind from
 * INTERNAL = 0 while the protocol reserves 0 for UNSPECIFIED, so a verbatim copy
 * labels every span one kind too low and nothing refuses it.
 */

function readableSpan(overrides: Partial<ReadableSpan> = {}): ReadableSpan {
  return {
    name: "page_view",
    kind: 0,
    spanContext: () => ({
      traceId: "00c0ffee00c0ffee00c0ffee00c0ffee",
      spanId: "00c0ffee00c0ffee",
      traceFlags: 1,
    }),
    startTime: [1788626299, 0],
    endTime: [1788626300, 500000000],
    status: { code: 0 },
    attributes: { "url.path": "/checkout", "browser.mobile": true, count: 3 },
    links: [],
    events: [],
    duration: [1, 500000000],
    ended: true,
    droppedAttributesCount: 0,
    droppedEventsCount: 0,
    droppedLinksCount: 0,
    ...overrides,
  } as ReadableSpan;
}

describe("toSpanJson", () => {
  it("shifts the API's span kind onto the protocol's numbering", () => {
    expect(toSpanJson(readableSpan()).kind).toBe(1);
    expect(toSpanJson(readableSpan({ kind: 2 })).kind).toBe(3);
  });

  it("sends ids as lower-case hex, straight from the span context", () => {
    const span = toSpanJson(readableSpan());
    expect(span.traceId).toBe("00c0ffee00c0ffee00c0ffee00c0ffee");
    expect(span.traceId).toHaveLength(32);
    expect(span.spanId).toHaveLength(16);
  });

  it("omits parentSpanId when the span is a root", () => {
    expect(toSpanJson(readableSpan())).not.toHaveProperty("parentSpanId");
  });

  it("writes both timestamps as nanosecond strings", () => {
    const span = toSpanJson(readableSpan());
    expect(span.startTimeUnixNano).toBe("1788626299000000000");
    expect(span.endTimeUnixNano).toBe("1788626300500000000");
  });

  it("carries each attribute type into its OTLP value shape", () => {
    expect(toSpanJson(readableSpan()).attributes).toEqual([
      { key: "url.path", value: { stringValue: "/checkout" } },
      { key: "browser.mobile", value: { boolValue: true } },
      { key: "count", value: { doubleValue: 3 } },
    ]);
  });
});

describe("tracesExport", () => {
  it("puts the schema url on the scope, which is where the pipeline reads it", () => {
    const body = JSON.parse(tracesExport([], [toSpanJson(readableSpan())]));
    const scope = body.resourceSpans[0].scopeSpans[0];
    expect(scope.schemaUrl).toBe("https://opentelemetry.io/schemas/1.44.0");
    expect(scope.spans).toHaveLength(1);
  });
});
