import type { NexovueOptions } from "./config";
import { resolveConfig } from "./config";
import { FLUSH_INTERVAL_MS, SESSION_IDLE_TIMEOUT_MS, SESSION_MAX_DURATION_MS } from "./constants";
import { countFailure, failureCounts, guard, reportOnce } from "./diagnostics";
import { randomnessAvailable } from "./ids";
import type { NexovueRum } from "./rum";
import { startRum } from "./rum";

export type { NexovueOptions } from "./config";
export type { NexovueRum } from "./rum";

let running: NexovueRum | undefined;

/**
 * Starts real user monitoring for this page.
 *
 * It never throws and never returns undefined: a refused configuration hands
 * back an inert handle, so a customer calling init(...).flush() cannot be given
 * a TypeError by an observability script.
 *
 * Before any of it reaches storage the customer must list every origin their
 * pages are served from on the site key — including http://localhost:PORT for
 * development, and the apex as well as the wildcard if they use both. A key with
 * an empty origin list refuses 100% of traffic.
 */
export function init(options: NexovueOptions): NexovueRum {
  if (running) {
    reportOnce("double-init", "init() was called twice; the first call is still running.");
    return running;
  }
  try {
    if (!randomnessAvailable()) {
      reportOnce("crypto", "refusing to start: this browser has no crypto.getRandomValues.");
      return inert();
    }
    const config = resolveConfig(options, {
      sessionIdleTimeoutMs: SESSION_IDLE_TIMEOUT_MS,
      sessionMaxDurationMs: SESSION_MAX_DURATION_MS,
      flushIntervalMs: FLUSH_INTERVAL_MS,
    });
    if (!config) return inert();

    const rum = startRum(config);
    running = {
      active: true,
      flush: guard("rum.flush", rum.flush),
      diagnostics: failureCounts,
      shutdown: guard("rum.shutdown", () => {
        rum.shutdown();
        running = undefined;
      }),
    };
    return running;
  } catch {
    countFailure("init");
    return inert();
  }
}

function inert(): NexovueRum {
  return {
    active: false,
    flush: () => undefined,
    diagnostics: failureCounts,
    shutdown: () => undefined,
  };
}
