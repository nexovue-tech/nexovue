/**
 * The two numbers this screen repeats often enough to be worth a rule.
 *
 * Both are read at a glance from inside a small box, so the digit count matters more
 * than the precision: `4.8k` beats `4798.3` on a node that also has to hold a name.
 */

const THOUSAND = 1000;
const MILLION = 1_000_000;

/** Calls per minute, at a fixed width the eye can scan down a column of nodes. */
export function formatRate(perMinute: number): string {
  if (!Number.isFinite(perMinute) || perMinute <= 0) return "0";
  if (perMinute < 10) return trim(perMinute.toFixed(1));
  if (perMinute < THOUSAND) return String(Math.round(perMinute));
  if (perMinute < MILLION) return `${trim((perMinute / THOUSAND).toFixed(1))}k`;
  return `${trim((perMinute / MILLION).toFixed(1))}M`;
}

/**
 * An error rate as a percentage. Rates this low are the point — 158 calls in 20,543
 * is 0.77%, and rounding that to "1%" or, worse, "0%" hides the dependency someone
 * opened the screen to find.
 */
export function formatErrorRate(fraction: number): string {
  if (!Number.isFinite(fraction) || fraction <= 0) return "0%";

  const percent = fraction * 100;
  if (percent < 0.1) return "<0.1%";
  if (percent < 10) return `${trim(percent.toFixed(2))}%`;
  return `${Math.round(percent)}%`;
}

/** A trailing zero after the point is noise; a digit before it is not. */
function trim(fixed: string): string {
  return fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
}
