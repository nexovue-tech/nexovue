/**
 * An observability script that breaks the site it observes is worse than no
 * observability, so nothing here ever reaches the customer's page: every entry
 * point the browser can call into is wrapped, and a failure is counted rather
 * than thrown.
 */

const failures = new Map<string, number>();

/** Console output is reserved for what the customer must act on. */
const alreadyReported = new Set<string>();

export function countFailure(site: string): void {
  failures.set(site, (failures.get(site) ?? 0) + 1);
}

/** A snapshot of what has gone wrong inside the SDK, for support to ask for. */
export function failureCounts(): Record<string, number> {
  return Object.fromEntries(failures);
}

export function resetDiagnostics(): void {
  failures.clear();
  alreadyReported.clear();
}

/** Wraps a browser callback so a defect in it can never surface in the page. */
export function guard<Args extends unknown[]>(
  site: string,
  run: (...args: Args) => void,
): (...args: Args) => void {
  return (...args: Args) => {
    try {
      run(...args);
    } catch {
      countFailure(site);
    }
  };
}

/** The same for a promise-returning routine: a rejection is a failure, not a crash. */
export function guardAsync<Args extends unknown[], Result>(
  site: string,
  fallback: Result,
  run: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result> {
  return async (...args: Args) => {
    try {
      return await run(...args);
    } catch {
      countFailure(site);
      return fallback;
    }
  };
}

/**
 * Says something once, on the one channel a customer reads. Reserved for a
 * misconfiguration only they can fix — a wrong key, an origin their site key
 * does not allow, a CDN that mutes its own errors.
 */
export function reportOnce(topic: string, message: string): void {
  if (alreadyReported.has(topic)) return;
  alreadyReported.add(topic);
  console.error(`[nexovue] ${message}`);
}
