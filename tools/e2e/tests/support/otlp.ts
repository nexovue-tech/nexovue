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
const SPAN_KIND_CLIENT = 3;
const STATUS_CODE_OK = 1;
const STATUS_CODE_ERROR = 2;

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

  return {
    traceId: traceId.toString("hex"),
    caller,
    callee,
    batch: {
      resourceSpans: [
        resourceSpans(caller.service, [span(rootSpanId, caller.name, 0, ROOT_DURATION_MS)]),
        resourceSpans(callee.service, [
          span(childSpanId, callee.name, CHILD_OFFSET_MS, CHILD_DURATION_MS, rootSpanId),
        ]),
      ],
    },
  };
}

/** How many calls to send, and how many of them failed at both ends. */
export interface DependencyLoad {
  calls: number;
  failures: number;
}

export interface DependencyTraffic {
  batch: ExportBatch;
  /** Every call took exactly this long, so the edge's p95 is this value, not near it. */
  callDurationMs: number;
}

/**
 * The traffic that draws one edge on the service map: `load.calls` calls from `caller`
 * to `callee`, `load.failures` of them failing.
 *
 * An edge is derived from a CLIENT span paired with the SERVER span beneath it — one
 * call seen from both ends — so `crossServiceTrace` above, whose two spans are both
 * SERVER, draws a waterfall but no edge at all. Each call is its own trace, because the
 * rate the map reports counts calls and not traces.
 */
export function serviceDependency(
  caller: ReportedSpan,
  callee: ReportedSpan,
  load: DependencyLoad,
): DependencyTraffic {
  if (load.calls <= 0) throw new Error("a dependency needs at least one call");
  if (load.failures < 0 || load.failures > load.calls) {
    throw new Error(`${load.failures} failures is impossible in ${load.calls} calls`);
  }

  const startedAtMs = Date.now() - BACKDATE_MS;
  const clientSpans: WireSpan[] = [];
  const serverSpans: WireSpan[] = [];

  for (let call = 0; call < load.calls; call += 1) {
    const traceId = randomBytes(16);
    const clientSpanId = randomBytes(8);
    // Both ends of a failed call report the failure, the way an SDK that saw the
    // callee's 500 would.
    const status = { code: call < load.failures ? STATUS_CODE_ERROR : STATUS_CODE_OK };
    const timing = {
      traceId: toWireId(traceId),
      startTimeUnixNano: String(startedAtMs * NS_PER_MS),
      endTimeUnixNano: String((startedAtMs + ROOT_DURATION_MS) * NS_PER_MS),
      status,
    };

    clientSpans.push({
      ...timing,
      spanId: toWireId(clientSpanId),
      name: caller.name,
      kind: SPAN_KIND_CLIENT,
    });
    serverSpans.push({
      ...timing,
      spanId: toWireId(randomBytes(8)),
      parentSpanId: toWireId(clientSpanId),
      name: callee.name,
      kind: SPAN_KIND_SERVER,
    });
  }

  return {
    batch: {
      resourceSpans: [
        resourceSpans(caller.service, clientSpans),
        resourceSpans(callee.service, serverSpans),
      ],
    },
    // The edge's latency is the caller's own duration, and every call here is the
    // same length, so every quantile of it is that length.
    callDurationMs: ROOT_DURATION_MS,
  };
}

/** How many requests hit one endpoint, and how long each of them took. */
export interface EndpointLoad {
  count: number;
  durationMs: number;
}

/**
 * The traffic behind one row of the services overview: `succeeded.count` requests that
 * returned and `failed.count` that did not, all on the same endpoint of one service.
 *
 * Each request is its own single-span trace, because the per-minute rollup counts
 * requests rather than traces. Both groups are given explicit durations so the caller
 * can state the quantiles it expects: with two distinct values the p50 and the tail are
 * different numbers, which a single duration could never prove.
 */
export function endpointTraffic(options: {
  service: string;
  endpoint: string;
  succeeded: EndpointLoad;
  failed: EndpointLoad;
}): ExportBatch {
  const startedAtMs = Date.now() - BACKDATE_MS;

  const requests = (load: EndpointLoad, statusCode: number): WireSpan[] =>
    Array.from({ length: load.count }, () => ({
      traceId: toWireId(randomBytes(16)),
      spanId: toWireId(randomBytes(8)),
      name: options.endpoint,
      // SERVER, because the rollup behind the overview counts only the kinds that
      // are an endpoint someone called: SERVER and CONSUMER.
      kind: SPAN_KIND_SERVER,
      startTimeUnixNano: String(startedAtMs * NS_PER_MS),
      endTimeUnixNano: String((startedAtMs + load.durationMs) * NS_PER_MS),
      status: { code: statusCode },
    }));

  return {
    resourceSpans: [
      resourceSpans(options.service, [
        ...requests(options.succeeded, STATUS_CODE_OK),
        ...requests(options.failed, STATUS_CODE_ERROR),
      ]),
    ],
  };
}

/** One service's spans, tagged with the `service.name` the map names it by. */
function resourceSpans(service: string, spans: WireSpan[]): WireResourceSpans {
  return {
    resource: { attributes: [{ key: "service.name", value: { stringValue: service } }] },
    scopeSpans: [{ scope: { name: "nexovue-e2e" }, spans }],
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
  await postSignal(request, "/v1/traces", options, options.batch);
}

/* ------------------------------------------------------------------ log records
 *
 * Door 2, for the logs and errors screens. An exception reaches the error grouping
 * as a log record carrying `exception.type` / `exception.message` — the writer that
 * promotes those two columns is the log one, and only the log one (P1-30).
 */

/** OTLP severity numbers, from opentelemetry/proto/logs/v1/logs.proto. */
export const SEVERITY_INFO = 9;
export const SEVERITY_ERROR = 17;

/** A log record as a test talks about it: what it said, how loudly, and when. */
export interface ReportedLog {
  body: string;
  severityNumber: number;
  severityText: string;
  /** Flat OTLP names, e.g. `exception.type`. The store nests them; we do not. */
  attributes?: Record<string, string>;
  /** How long before now the record claims to have been written. */
  agoMs?: number;
}

interface WireLogRecord {
  timeUnixNano: string;
  observedTimeUnixNano: string;
  severityNumber: number;
  severityText: string;
  body: { stringValue: string };
  attributes: Array<{ key: string; value: { stringValue: string } }>;
}

interface WireResourceLogs {
  resource: { attributes: Array<{ key: string; value: { stringValue: string } }> };
  scopeLogs: Array<{ scope: { name: string }; logRecords: WireLogRecord[] }>;
}

export interface LogExportBatch {
  resourceLogs: WireResourceLogs[];
}

export interface LogExport {
  batch: LogExportBatch;
  /** What each record claims as its own clock, in the order they were given. */
  writtenAt: string[];
}

/** One service's worth of records, each stamped as far back as it asked to be. */
export function logBatch(service: string, records: readonly ReportedLog[]): LogExport {
  const now = Date.now();
  const stamped = records.map((record) => now - (record.agoMs ?? BACKDATE_MS));

  return {
    writtenAt: stamped.map((instant) => new Date(instant).toISOString()),
    batch: {
      resourceLogs: [
        {
          resource: { attributes: [attribute("service.name", service)] },
          scopeLogs: [
            {
              scope: { name: "nexovue-e2e" },
              logRecords: records.map((record, index) => toWireRecord(record, stamped[index], now)),
            },
          ],
        },
      ],
    },
  };
}

function toWireRecord(
  record: ReportedLog,
  writtenAtMs: number,
  observedAtMs: number,
): WireLogRecord {
  return {
    timeUnixNano: String(Math.round(writtenAtMs * NS_PER_MS)),
    observedTimeUnixNano: String(Math.round(observedAtMs * NS_PER_MS)),
    severityNumber: record.severityNumber,
    severityText: record.severityText,
    body: { stringValue: record.body },
    attributes: Object.entries(record.attributes ?? {}).map(([key, value]) =>
      attribute(key, value),
    ),
  };
}

function attribute(key: string, value: string): { key: string; value: { stringValue: string } } {
  return { key, value: { stringValue: value } };
}

/** Exports the records and asserts the receiver accepted every one of them. */
export async function exportLogs(
  request: APIRequestContext,
  options: { endpoint: string; apiKey: string; batch: LogExportBatch },
): Promise<void> {
  await postSignal(request, "/v1/logs", options, options.batch);
}

/**
 * One OTLP/HTTP export, checked. An empty response body is OTLP's "all of it,
 * nothing partial" — a partial success would name what it dropped.
 */
async function postSignal(
  request: APIRequestContext,
  path: string,
  credentials: { endpoint: string; apiKey: string },
  payload: unknown,
): Promise<void> {
  const response = await request.post(new URL(path, credentials.endpoint).toString(), {
    headers: { "content-type": "application/json", "x-nexovue-key": credentials.apiKey },
    data: payload,
  });

  const body = await response.text();
  expect(response.status(), `receiver refused the batch: ${body}`).toBe(200);
  expect(JSON.parse(body)).toEqual({});
}

/** One stack, root last as OTLP orders them, and the CPU it accounts for. */
export interface ProfiledStack {
  frames: readonly string[];
  nanoseconds: number;
}

/**
 * One CPU profile of `service`, as OTLP/JSON profiles v1development: every string and
 * frame is an index into the request's dictionary, entry zero of each table empty.
 */
export function profileBatch(service: string, stacks: readonly ProfiledStack[]) {
  const strings = ["", "cpu", "nanoseconds"];
  const functionIndex = new Map<string, number>();
  const functionTable: object[] = [{}];
  const locationTable: object[] = [{}];
  const stackTable: object[] = [{}];
  const indexOf = (name: string) => {
    let found = functionIndex.get(name);
    if (found === undefined) {
      strings.push(name);
      functionTable.push({ nameStrindex: strings.length - 1 });
      locationTable.push({ lines: [{ functionIndex: functionTable.length - 1 }] });
      found = locationTable.length - 1;
      functionIndex.set(name, found);
    }
    return found;
  };
  const samples = stacks.map((stack) => {
    stackTable.push({ locationIndices: stack.frames.map(indexOf) });
    return { stackIndex: stackTable.length - 1, values: [String(stack.nanoseconds)] };
  });
  const now = BigInt(Date.now() - 1_000) * 1_000_000n;
  return {
    dictionary: { stringTable: strings, functionTable, locationTable, stackTable },
    resourceProfiles: [
      {
        resource: { attributes: [{ key: "service.name", value: { stringValue: service } }] },
        scopeProfiles: [
          {
            profiles: [
              {
                sampleType: { typeStrindex: 1, unitStrindex: 2 },
                timeUnixNano: now.toString(),
                samples,
              },
            ],
          },
        ],
      },
    ],
  };
}

export async function exportProfiles(
  request: APIRequestContext,
  options: { endpoint: string; apiKey: string; batch: object },
): Promise<void> {
  await postSignal(request, "/v1development/profiles", options, options.batch);
}

/**
 * A heavy CPU profile of `service`: `stacks` distinct stacks, `depth` frames deep,
 * over 400 long function names — the size a busy JVM service sends, enough to fill
 * the flame graph to its bound and the functions table past a screen.
 */
export function heavyProfileBatch(service: string, stacks: number, depth: number) {
  const strings = ["", "cpu", "nanoseconds"];
  const functionTable: object[] = [{}];
  const locationTable: object[] = [{}];
  for (let index = 0; index < 400; index++) {
    strings.push(
      `com.example.module${index % 12}.Service${index}.handleRequestWithAVeryLongName${index}`,
    );
    functionTable.push({ nameStrindex: strings.length - 1 });
    locationTable.push({ lines: [{ functionIndex: functionTable.length - 1 }] });
  }
  const stackTable: object[] = [{}];
  const samples: object[] = [];
  for (let stack = 0; stack < stacks; stack++) {
    const frames: number[] = [];
    for (let frame = 0; frame < depth; frame++) {
      frames.push(1 + ((stack * 7 + frame * 13 + (frame > depth - 4 ? stack : 0)) % 400));
    }
    stackTable.push({ locationIndices: frames });
    samples.push({
      stackIndex: stackTable.length - 1,
      values: [String(1_000_000 * (1 + (stack % 17)))],
    });
  }
  const now = BigInt(Date.now() - 60_000) * 1_000_000n;
  return {
    dictionary: { stringTable: strings, functionTable, locationTable, stackTable },
    resourceProfiles: [
      {
        resource: { attributes: [{ key: "service.name", value: { stringValue: service } }] },
        scopeProfiles: [
          {
            profiles: [
              {
                sampleType: { typeStrindex: 1, unitStrindex: 2 },
                timeUnixNano: now.toString(),
                samples,
              },
            ],
          },
        ],
      },
    ],
  };
}
