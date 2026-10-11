/**
 * The production log sink: the console's warnings and errors, sent to the API's log
 * (`POST /api/v1/client-logs`, ADR-021). Before this, production dropped every one.
 *
 * Batched for a moment so a burst is one request, sent with `keepalive` so a record
 * written as the page unloads still leaves, and held to one batch so an error storm
 * cannot grow memory. A failed send is dropped silently: logging it would feed this
 * sink, and the sink would send it again.
 */

import { API_BASE_PATH } from "./api-client";
import type { LogRecord, LogSink } from "./logger";

export const CLIENT_LOGS_PATH = `${API_BASE_PATH}client-logs`;

/** The API refuses a larger batch; beyond it, records are dropped until the next send. */
export const MAX_BATCH = 20;

const SEND_AFTER_MS = 2_000;

interface Options {
  send?: (url: string, init: RequestInit) => Promise<unknown>;
  target?: EventTarget;
  page?: () => string;
}

export function createBeaconSink({
  send = (url, init) => fetch(url, init),
  target = window,
  page = () => window.location.pathname,
}: Options = {}): LogSink {
  let held: object[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;

  function flush() {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    if (held.length === 0) return;
    const records = held;
    held = [];
    send(CLIENT_LOGS_PATH, {
      method: "POST",
      keepalive: true,
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ records }),
    }).catch(() => {});
  }

  target.addEventListener("pagehide", flush);

  return (record: LogRecord) => {
    if (record.severity !== "warn" && record.severity !== "error") return;
    if (held.length >= MAX_BATCH) return;
    held.push({
      severity: record.severity,
      message: record.message,
      timestamp: record.timestamp,
      page: page(),
      error: record.error,
      attributes: record.attributes,
      trace_id: record.trace_id,
      correlation_id: record.correlation_id,
    });
    timer ??= setTimeout(flush, SEND_AFTER_MS);
  };
}
