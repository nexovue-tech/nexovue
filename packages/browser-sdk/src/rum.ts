import type { ReadableSpan, SpanProcessor } from "@opentelemetry/sdk-trace-web";
import { WebTracerProvider } from "@opentelemetry/sdk-trace-web";

import type { ResolvedConfig } from "./config";
import { LOGS_PATH, PAGE_VIEW_SPAN_NAME, SDK_NAME, SDK_VERSION, TRACES_PATH } from "./constants";
import { captureErrors } from "./errors";
import { createExporter } from "./exporter";
import { observeLifecycle } from "./lifecycle";
import { browserResource } from "./otlp/resource";
import { toSpanJson } from "./otlp/spans";
import { pageViewAttributes } from "./pagecontext";
import { navigationStartedAt, whenPageLoaded } from "./pageview";
import { errorRecord, webVitalRecord } from "./records";
import { createSession } from "./session";
import { endpointsFor } from "./transport";
import { reportWebVitals } from "./vitals";

/** A running SDK. Every method is already wrapped; none of them can throw. */
export interface NexovueRum {
  /** True when telemetry is being collected. False when init refused the configuration. */
  readonly active: boolean;
  /** Sends whatever is buffered now, rather than waiting for the next interval. */
  flush(): void;
  /** What has gone wrong inside the SDK, by site. For support to ask for. */
  diagnostics(): Record<string, number>;
  /** Detaches every listener and stops collecting. */
  shutdown(): void;
}

export interface Collector {
  flush(): void;
  shutdown(): void;
}

/** The collecting half. index.ts wraps it in the handle the page is given. */
export function startRum(config: ResolvedConfig): Collector {
  const session = createSession({
    siteKey: config.siteKey,
    idleTimeoutMs: config.sessionIdleTimeoutMs,
    maxDurationMs: config.sessionMaxDurationMs,
  });
  const exporter = createExporter({
    siteKey: config.siteKey,
    resource: browserResource(config),
    logsEndpoints: endpointsFor(config.endpoint, LOGS_PATH, config.siteKey),
    tracesEndpoints: endpointsFor(config.endpoint, TRACES_PATH, config.siteKey),
  });
  const sessionId = () => session.currentId(Date.now());

  const provider = new WebTracerProvider({
    spanProcessors: [collectingProcessor((span) => exporter.enqueueSpan(toSpanJson(span)))],
  });
  const tracer = provider.getTracer(SDK_NAME, SDK_VERSION);

  // Registered before the lifecycle listener, so web-vitals' own window-capture
  // visibilitychange handler runs before our document-target flush.
  reportWebVitals((metric) => exporter.enqueueLog(webVitalRecord(metric, sessionId(), config)));
  const detachErrors = captureErrors((error) =>
    exporter.enqueueLog(errorRecord(error, sessionId(), config)),
  );
  const cancelPageView = whenPageLoaded((loadedAt) => {
    const span = tracer.startSpan(PAGE_VIEW_SPAN_NAME, {
      startTime: navigationStartedAt(),
      attributes: pageViewAttributes(config, sessionId()),
    });
    span.end(loadedAt);
  });
  const detachLifecycle = observeLifecycle({
    onExit: () => exporter.flushOnExit(),
    onInterval: () => exporter.flush(),
    intervalMs: config.flushIntervalMs,
  });

  return {
    flush: () => exporter.flush(),
    shutdown: () => {
      detachLifecycle();
      detachErrors();
      cancelPageView();
      void provider.shutdown();
    },
  };
}

/**
 * Hands every finished span straight to our own exporter. The SDK's own batching
 * processor is not used: one queue decides what leaves the page, so that the
 * unload flush can be a single beacon within the browser's cumulative quota.
 */
function collectingProcessor(collect: (span: ReadableSpan) => void): SpanProcessor {
  return {
    onStart: () => undefined,
    onEnd: collect,
    forceFlush: () => Promise.resolve(),
    shutdown: () => Promise.resolve(),
  };
}
