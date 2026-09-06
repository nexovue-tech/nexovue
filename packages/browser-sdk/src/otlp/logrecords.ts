import { SDK_NAME, SDK_VERSION, SEMCONV_SCHEMA_URL } from "../constants";
import type { AnyValue, KeyValue } from "./values";

/**
 * A hand-built OTLP log record. Web vitals and JS errors travel this way rather
 * than as span attributes: log records are written straight to storage, while
 * traces pass a tail sampler that keeps roughly one healthy trace in ten.
 *
 * Trace context is deliberately absent from the type. The Errors screen counts
 * distinct (trace id, span id) pairs per group, so ten errors sharing one page's
 * span context would be counted as one occurrence.
 */
export interface LogRecordJson {
  timeUnixNano: string;
  observedTimeUnixNano: string;
  severityNumber: number;
  severityText: string;
  eventName: string;
  body?: AnyValue;
  attributes: KeyValue[];
}

export function logsExport(resource: KeyValue[], logRecords: LogRecordJson[]): string {
  return JSON.stringify({
    resourceLogs: [
      {
        resource: { attributes: resource },
        scopeLogs: [
          {
            scope: { name: SDK_NAME, version: SDK_VERSION },
            schemaUrl: SEMCONV_SCHEMA_URL,
            logRecords,
          },
        ],
      },
    ],
  });
}
