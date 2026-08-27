/**
 * Sending a trace the way a customer's SDK would: OTLP/HTTP to door 1, authenticated
 * with an ingest key.
 *
 * The batch is deliberately two resources rather than one. A single-service trace only
 * proves the waterfall can draw a list; a parent in one service and its child in another
 * is the thing the product exists to show, and nothing but a real edge can fake it.
 */

import { randomBytes } from "node:crypto";

import { type APIRequestContext, expect } from "@playwright/test";

const NS_PER_MS = 1_000_000;

/** OTLP enum values, from opentelemetry/proto/trace/v1/trace.proto. */
const SPAN_KIND_SERVER = 2;
const STATUS_CODE_OK = 1;

/** How long the request took, and how much of it the callee was responsible for. */
const ROOT_DURATION_MS = 240;
const CHILD_OFFSET_MS = 30;
const CHILD_DURATION_MS = 120;

/**
 * Far enough in the past that a clock skew of a few hundred milliseconds between this
 * machine and the API cannot put the trace after the query window's `to`.
 */
const BACKDATE_MS = 1_000;

/** One span as the wire carries it. Ids are base64 — see `toWireId`. */
interface WireSpan {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  kind: number;
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  status: { code: number };
}

interface WireResourceSpans {
  resource: { attributes: Array<{ key: string; value: { stringValue: string } }> };
  scopeSpans: Array<{ scope: { name: string }; spans: WireSpan[] }>;
}

export interface ExportBatch {
  resourceSpans: WireResourceSpans[];
}

/** A span as a test talks about it: the service that reported it and what it was called. */
export interface ReportedSpan {
  service: string;
  name: string;
}

export interface CrossServiceTrace {
  /** Canonical hex, which is how the API and the browser URL spell it. */
  traceId: string;
  caller: ReportedSpan;
  callee: ReportedSpan;
  batch: ExportBatch;
}

/**
 * OTLP/JSON spells trace and span ids as hex, but this receiver decodes the body with
 * plain protojson, which reads a `bytes` field as base64. Hex is rejected outright, so
 * the wire form here is base64. See the P1-60 report: that is a receiver defect, not a
 * property of the protocol.
 */
function toWireId(bytes: Buffer): string {
  return bytes.toString("base64");
}

/** One request in `caller` that spends most of its time waiting on `callee`. */
export function crossServiceTrace(caller: ReportedSpan, callee: ReportedSpan): CrossServiceTrace {
  const traceId = randomBytes(16);
  const rootSpanId = randomBytes(8);
  const childSpanId = randomBytes(8);
  const startedAtMs = Date.now() - BACKDATE_MS;

  const span = (
    spanId: Buffer,
    name: string,
    offsetMs: number,
    durationMs: number,
    parentSpanId?: Buffer,
  ): WireSpan => ({
    traceId: toWireId(traceId),
    spanId: toWireId(spanId),
    ...(parentSpanId ? { parentSpanId: toWireId(parentSpanId) } : {}),
    name,
    kind: SPAN_KIND_SERVER,
    startTimeUnixNano: String(Math.round((startedAtMs + offsetMs) * NS_PER_MS)),
    endTimeUnixNano: String(Math.round((startedAtMs + offsetMs + durationMs) * NS_PER_MS)),
    status: { code: STATUS_CODE_OK },
  });

  const resource = (service: string, spans: WireSpan[]): WireResourceSpans => ({
    resource: { attributes: [{ key: "service.name", value: { stringValue: service } }] },
    scopeSpans: [{ scope: { name: "nexovue-e2e" }, spans }],
  });

  return {
    traceId: traceId.toString("hex"),
    caller,
    callee,
    batch: {
      resourceSpans: [
        resource(caller.service, [span(rootSpanId, caller.name, 0, ROOT_DURATION_MS)]),
        resource(callee.service, [
          span(childSpanId, callee.name, CHILD_OFFSET_MS, CHILD_DURATION_MS, rootSpanId),
        ]),
      ],
    },
  };
}

/**
 * Exports the batch and asserts the receiver accepted it. An empty
 * ExportTraceServiceResponse is OTLP's "all of it, nothing partial".
 */
export async function exportTrace(
  request: APIRequestContext,
  options: { endpoint: string; apiKey: string; batch: ExportBatch },
): Promise<void> {
  const response = await request.post(new URL("/v1/traces", options.endpoint).toString(), {
    headers: { "content-type": "application/json", "x-nexovue-key": options.apiKey },
    data: options.batch,
  });

  const body = await response.text();
  expect(response.status(), `receiver refused the batch: ${body}`).toBe(200);
  expect(JSON.parse(body)).toEqual({});
}
