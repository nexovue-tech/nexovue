import type { Metric } from "web-vitals";

import {
  EXCEPTION_EVENT_NAME,
  SEVERITY_ERROR,
  SEVERITY_INFO,
  WEB_VITAL_EVENT_NAME,
} from "./constants";
import type { CapturedError } from "./errors";
import type { LogRecordJson } from "./otlp/logrecords";
import { AttributeList } from "./otlp/values";
import { addPageContext } from "./pagecontext";
import {
  ATTR_CODE_COLUMN_NUMBER,
  ATTR_CODE_FILE_PATH,
  ATTR_CODE_LINE_NUMBER,
  ATTR_EXCEPTION_MESSAGE,
  ATTR_EXCEPTION_STACKTRACE,
  ATTR_EXCEPTION_TYPE,
  ATTR_WEB_VITAL_DELTA,
  ATTR_WEB_VITAL_ID,
  ATTR_WEB_VITAL_NAME,
  ATTR_WEB_VITAL_NAVIGATION_TYPE,
  ATTR_WEB_VITAL_RATING,
  ATTR_WEB_VITAL_VALUE,
} from "./semconv";
import { nowNanos } from "./time";

/**
 * A web vital as one OTLP log record.
 *
 * The value is passed through unchanged — milliseconds for lcp, inp, fcp and
 * ttfb, a unitless ratio for cls. That is what every OpenTelemetry browser
 * instrumentation does, so a customer's existing thresholds keep their meaning;
 * the conventions state no unit at all.
 *
 * metric.entries is never serialised. It holds raw PerformanceEntry objects and
 * the INP one in particular can be very large.
 */
export function webVitalRecord(metric: Metric, sessionId: string): LogRecordJson {
  const attributes = new AttributeList()
    .string(ATTR_WEB_VITAL_NAME, metric.name.toLowerCase())
    .double(ATTR_WEB_VITAL_VALUE, metric.value)
    .double(ATTR_WEB_VITAL_DELTA, metric.delta)
    .string(ATTR_WEB_VITAL_ID, metric.id)
    .string(ATTR_WEB_VITAL_RATING, metric.rating)
    // Passed through as reported, "soft-navigation" included: it is outside the
    // conventions' well-known list, and rewriting it would record something untrue.
    .string(ATTR_WEB_VITAL_NAVIGATION_TYPE, metric.navigationType);
  const at = nowNanos();
  return {
    timeUnixNano: at,
    observedTimeUnixNano: at,
    severityNumber: SEVERITY_INFO,
    severityText: "INFO",
    eventName: WEB_VITAL_EVENT_NAME,
    attributes: addPageContext(attributes, sessionId).build(),
  };
}

/**
 * A thrown error as one OTLP log record.
 *
 * No trace context, deliberately. The Errors screen counts distinct (trace id,
 * span id) pairs within a group, so stamping every error on a page with the
 * page-view span's context would report two hundred errors as one occurrence.
 *
 * The body repeats the message because it is the column carrying a text index;
 * the attribute copy is what fills exception_message.
 */
export function errorRecord(error: CapturedError, sessionId: string): LogRecordJson {
  const attributes = new AttributeList()
    .string(ATTR_EXCEPTION_TYPE, error.type)
    .string(ATTR_EXCEPTION_MESSAGE, error.message)
    .string(ATTR_EXCEPTION_STACKTRACE, error.stacktrace)
    .string(ATTR_CODE_FILE_PATH, error.filePath)
    .int(ATTR_CODE_LINE_NUMBER, error.line)
    .int(ATTR_CODE_COLUMN_NUMBER, error.column);
  const at = nowNanos();
  return {
    timeUnixNano: at,
    observedTimeUnixNano: at,
    severityNumber: SEVERITY_ERROR,
    severityText: "ERROR",
    eventName: EXCEPTION_EVENT_NAME,
    body: { stringValue: error.message },
    attributes: addPageContext(attributes, sessionId).build(),
  };
}
