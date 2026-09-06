import type { AttributeValue } from "@opentelemetry/api";
import type { ReadableSpan } from "@opentelemetry/sdk-trace-web";

import { SDK_NAME, SDK_VERSION, SEMCONV_SCHEMA_URL } from "../constants";
import { hrTimeToNanos } from "../time";
import { AttributeList, type KeyValue } from "./values";

/**
 * The JavaScript API numbers SpanKind from INTERNAL = 0; the OTLP protocol
 * reserves 0 for UNSPECIFIED and numbers INTERNAL = 1. Sending the API's value
 * verbatim would label every span one kind too low.
 */
const OTLP_SPAN_KIND_OFFSET = 1;

export interface SpanJson {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  kind: number;
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  attributes: KeyValue[];
  status: { code: number };
}

/** Ids come from the span's own context, never hand-built: one span carrying a
 * trace id that is not 16 bytes refuses the whole export, well-formed spans
 * beside it included. */
export function toSpanJson(span: ReadableSpan): SpanJson {
  const context = span.spanContext();
  const parent = span.parentSpanContext?.spanId;
  return {
    traceId: context.traceId,
    spanId: context.spanId,
    ...(parent ? { parentSpanId: parent } : {}),
    name: span.name,
    kind: span.kind + OTLP_SPAN_KIND_OFFSET,
    startTimeUnixNano: hrTimeToNanos(span.startTime),
    endTimeUnixNano: hrTimeToNanos(span.endTime),
    attributes: toKeyValues(span.attributes),
    status: { code: span.status.code },
  };
}

export function tracesExport(resource: KeyValue[], spans: SpanJson[]): string {
  return JSON.stringify({
    resourceSpans: [
      {
        resource: { attributes: resource },
        scopeSpans: [
          {
            scope: { name: SDK_NAME, version: SDK_VERSION },
            schemaUrl: SEMCONV_SCHEMA_URL,
            spans,
          },
        ],
      },
    ],
  });
}

function toKeyValues(attributes: Record<string, AttributeValue | undefined>): KeyValue[] {
  const list = new AttributeList();
  for (const [key, value] of Object.entries(attributes)) {
    if (typeof value === "string") list.string(key, value);
    else if (typeof value === "number") list.double(key, value);
    else if (typeof value === "boolean") list.bool(key, value);
  }
  return list.build();
}
