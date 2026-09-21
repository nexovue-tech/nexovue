/**
 * Each worker is its own customer.
 *
 * Signup and login are counted per client address, and every browser here reaches
 * the API through the web container's proxy — so all four workers arrived as one
 * address, shared one allowance of ten a minute, and the harness paced itself
 * against it. Measured 2026-09-20: ~17 auth requests against 8/minute is a floor
 * of two minutes, which was most of the suite's three
 * (`docs/changes/faster-gate/`).
 *
 * Four workers *are* four callers — four developers on four laptops is the same
 * shape — so each presents an address of its own. The limit is not widened: a
 * worker still gets ten a minute and is still throttled if it spends them faster,
 * which is the behaviour the pacing exists to respect.
 *
 * It works because the development API is told there is one proxy in front of it
 * (`TRUSTED_PROXY_COUNT: 1`), which is true of this topology. Deployment targets
 * set that from their own, and nothing here reaches them.
 */

/** Playwright gives each worker process its index; a single-worker run is 0. */
export function workerIndex(): number {
  return Number(process.env.TEST_PARALLEL_INDEX ?? 0);
}

/** A private address per worker. Documentation range (RFC 5737), never routable. */
export function workerAddress(): string {
  return `192.0.2.${workerIndex() + 1}`;
}
