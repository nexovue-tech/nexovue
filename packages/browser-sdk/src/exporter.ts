import {
  BATCH_MAX_BYTES,
  PENDING_RECORDS_LIMIT,
  RETRY_BASE_DELAY_MS,
  RETRY_MAX_ATTEMPTS,
  RETRY_MAX_DELAY_MS,
} from "./constants";
import { countFailure } from "./diagnostics";
import type { LogRecordJson } from "./otlp/logrecords";
import { logsExport } from "./otlp/logrecords";
import type { SpanJson } from "./otlp/spans";
import { tracesExport } from "./otlp/spans";
import type { KeyValue } from "./otlp/values";
import type { Endpoints } from "./transport";
import { beaconExport, keepaliveExport, postExport } from "./transport";

/**
 * Buffers records and decides when they leave. Batching is by TIME, not by
 * trace: the receiver splits a traces export into one broker record per trace id
 * itself, and the per-key request budget — 1000/s shared by every visitor of the
 * site at once — makes one request per vital or per error untenable.
 */
export interface Exporter {
  enqueueLog(record: LogRecordJson): void;
  enqueueSpan(span: SpanJson): void;
  /** Normal flush over fetch, whose refusals are readable. */
  flush(): void;
  /** Last flush of the page, over sendBeacon. Nothing comes back. */
  flushOnExit(): void;
  hasStopped(): boolean;
}

/** Each record is weighed once, on the way in, so the queue's size is exact. */
interface Entry<Record> {
  value: Record;
  bytes: number;
}

interface Signal<Record> {
  readonly endpoints: Endpoints;
  readonly serialize: (records: Record[]) => string;
  entries: Entry<Record>[];
  bytes: number;
  attempts: number;
  pausedUntil: number;
  inFlight: boolean;
}

export interface ExporterOptions {
  siteKey: string;
  resource: KeyValue[];
  logsEndpoints: Endpoints;
  tracesEndpoints: Endpoints;
}

const encoder = new TextEncoder();

export function createExporter(options: ExporterOptions): Exporter {
  const resource = options.resource;
  const logs = newSignal<LogRecordJson>(options.logsEndpoints, (records) =>
    logsExport(resource, records),
  );
  const spans = newSignal<SpanJson>(options.tracesEndpoints, (records) =>
    tracesExport(resource, records),
  );
  let stopped = false;

  function add<Record>(signal: Signal<Record>, value: Record): void {
    if (stopped) return;
    const bytes = encoder.encode(JSON.stringify(value)).byteLength;
    signal.entries.push({ value, bytes });
    signal.bytes += bytes;
    // A paused or blocked queue must not grow without bound in the customer's tab.
    while (signal.entries.length > PENDING_RECORDS_LIMIT) {
      const dropped = signal.entries.shift();
      if (dropped) signal.bytes -= dropped.bytes;
      countFailure("exporter.overflow");
    }
    if (signal.bytes >= BATCH_MAX_BYTES) void drain(signal);
  }

  async function drain<Record>(signal: Signal<Record>): Promise<void> {
    if (stopped || signal.inFlight || signal.entries.length === 0) return;
    if (Date.now() < signal.pausedUntil) return;

    const sending = takeAll(signal);
    signal.inFlight = true;
    try {
      const body = signal.serialize(sending.map((entry) => entry.value));
      const result = await postExport(signal.endpoints, options.siteKey, body);
      if (result.outcome === "stop") {
        stopped = true;
        takeAll(signal);
        return;
      }
      if (result.outcome === "retry" && signal.attempts < RETRY_MAX_ATTEMPTS) {
        requeue(signal, sending);
        signal.pausedUntil = Date.now() + backoffMs(signal.attempts, result.retryAfterMs);
        signal.attempts += 1;
        return;
      }
      if (result.outcome !== "accepted") countFailure(`exporter.${result.outcome}`);
      signal.attempts = 0;
    } finally {
      signal.inFlight = false;
    }
  }

  function exit<Record>(signal: Signal<Record>): void {
    if (stopped || signal.entries.length === 0) return;
    const body = signal.serialize(takeAll(signal).map((entry) => entry.value));
    if (beaconExport(signal.endpoints, body)) return;
    // Over the browser's cumulative keepalive quota, or beacons unavailable.
    countFailure("exporter.beacon_refused");
    keepaliveExport(signal.endpoints, body);
  }

  return {
    enqueueLog: (record) => add(logs, record),
    enqueueSpan: (span) => add(spans, span),
    flush: () => {
      void drain(logs);
      void drain(spans);
    },
    // Logs first: a vital or an error cannot be reconstructed, a page-view span
    // largely can, and the beacon quota is cumulative so the second may be refused.
    flushOnExit: () => {
      exit(logs);
      exit(spans);
    },
    hasStopped: () => stopped,
  };
}

function newSignal<Record>(
  endpoints: Endpoints,
  serialize: (records: Record[]) => string,
): Signal<Record> {
  return {
    endpoints,
    serialize,
    entries: [],
    bytes: 0,
    attempts: 0,
    pausedUntil: 0,
    inFlight: false,
  };
}

function takeAll<Record>(signal: Signal<Record>): Entry<Record>[] {
  const taken = signal.entries;
  signal.entries = [];
  signal.bytes = 0;
  return taken;
}

/** Puts a refused batch back in front of whatever arrived while it was in flight. */
function requeue<Record>(signal: Signal<Record>, sending: Entry<Record>[]): void {
  signal.entries = sending.concat(signal.entries);
  signal.bytes = signal.entries.reduce((total, entry) => total + entry.bytes, 0);
}

/** Retry-After when the door named one, otherwise exponential with jitter, so a
 * throttled site does not resynchronise into a thundering herd. */
function backoffMs(attempts: number, retryAfterMs: number | undefined): number {
  if (retryAfterMs !== undefined) return retryAfterMs;
  const capped = Math.min(RETRY_BASE_DELAY_MS * 2 ** attempts, RETRY_MAX_DELAY_MS);
  return capped * (0.5 + Math.random() / 2);
}
