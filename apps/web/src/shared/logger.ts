/**
 * The one place in the web app allowed to touch the console.
 *
 * Records are shaped now so that the production transport is a wiring change rather
 * than a rewrite: the same fields the Go and Python services emit, so a browser log
 * and a receiver log line up in one query. The canonical cross-language contract is
 * owned by the backend half of P1-66 and must be reconciled with this shape.
 */

export type Severity = "debug" | "info" | "warn" | "error";

export type LogAttributes = Record<string, unknown>;

export interface LogRecord {
  timestamp: string;
  severity: Severity;
  message: string;
  service: string;
  attributes?: LogAttributes;
  trace_id?: string;
  correlation_id?: string;
  error?: { name: string; message: string; stack?: string };
}

/** Where records go: the devtools console in development, `log-beacon.ts` in production. */
export type LogSink = (record: LogRecord) => void;

const SERVICE_NAME = "nexovue-web";
const SEVERITY_ORDER: Record<Severity, number> = { debug: 10, info: 20, warn: 30, error: 40 };

function describeError(cause: unknown): LogRecord["error"] {
  if (cause instanceof Error) {
    return { name: cause.name, message: cause.message, stack: cause.stack };
  }
  if (cause === undefined) return undefined;
  return { name: "NonError", message: String(cause) };
}

/** Devtools filters by console method, so a debug line must not arrive as an error. */
const CONSOLE_METHOD: Record<Severity, "debug" | "info" | "warn" | "error"> = {
  debug: "debug",
  info: "info",
  warn: "warn",
  error: "error",
};

/** Development sink. This module is the one place in the app allowed to reach console. */
const consoleSink: LogSink = (record) => {
  const line = `${record.severity.toUpperCase()} ${record.message}`;
  // biome-ignore lint/suspicious/noConsole: this module is the app's console boundary.
  console[CONSOLE_METHOD[record.severity]](line, record);
};

/** Until `main.tsx` installs the production sink; tests run without one. */
const noopSink: LogSink = () => {};

let sink: LogSink = import.meta.env.DEV ? consoleSink : noopSink;
let minimumSeverity: Severity = import.meta.env.DEV ? "debug" : "info";

/** Installs the production transport (`main.tsx`). */
export function configureLogger(options: { sink?: LogSink; minimumSeverity?: Severity }): void {
  if (options.sink) sink = options.sink;
  if (options.minimumSeverity) minimumSeverity = options.minimumSeverity;
}

function emit(severity: Severity, message: string, attributes?: LogAttributes, cause?: unknown) {
  if (SEVERITY_ORDER[severity] < SEVERITY_ORDER[minimumSeverity]) return;

  const { trace_id, correlation_id, ...rest } = (attributes ?? {}) as LogAttributes & {
    trace_id?: string;
    correlation_id?: string;
  };

  sink({
    timestamp: new Date().toISOString(),
    severity,
    message,
    service: SERVICE_NAME,
    attributes: Object.keys(rest).length > 0 ? rest : undefined,
    trace_id,
    correlation_id,
    error: describeError(cause),
  });
}

export const logger = {
  debug: (message: string, attributes?: LogAttributes) => emit("debug", message, attributes),
  info: (message: string, attributes?: LogAttributes) => emit("info", message, attributes),
  warn: (message: string, attributes?: LogAttributes) => emit("warn", message, attributes),
  error: (message: string, cause?: unknown, attributes?: LogAttributes) =>
    emit("error", message, attributes, cause),
};
