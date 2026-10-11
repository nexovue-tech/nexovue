/**
 * Which sessions send their page views and vitals (group 9, D4).
 *
 * Decided per session, from the session id, so a session is kept whole — every
 * page view and vital of a journey, or none of them. Errors are never sampled:
 * they are what a customer most needs and least has.
 */

const BUCKETS = 10_000;

/** FNV-1a: small, fast, and spread well enough over ids for a share. */
function bucketOf(sessionId: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < sessionId.length; i++) {
    hash ^= sessionId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) % BUCKETS;
}

export function inSample(sessionId: string, rate: number): boolean {
  if (rate >= 1) return true;
  if (rate <= 0) return false;
  return bucketOf(sessionId) < rate * BUCKETS;
}
