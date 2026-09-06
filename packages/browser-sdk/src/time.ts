import type { HrTime } from "@opentelemetry/api";

/**
 * OTLP timestamps are 64-bit nanoseconds. A nanosecond epoch is around 1.79e18,
 * far past JavaScript's 2^53 safe integer, so a Number is already rounded before
 * JSON.stringify ever runs — the door would accept a timestamp wrong by hundreds
 * of nanoseconds. Every timestamp on the wire is therefore built as a string.
 */

const NANOS_PER_MILLI = 1_000_000n;
const NANOS_PER_SECOND = 1_000_000_000n;

export function epochMillisToNanos(epochMillis: number): string {
  const wholeMillis = Math.floor(epochMillis);
  const subMilliNanos = Math.round((epochMillis - wholeMillis) * 1_000_000);
  return (BigInt(wholeMillis) * NANOS_PER_MILLI + BigInt(subMilliNanos)).toString();
}

export function hrTimeToNanos(time: HrTime): string {
  return (BigInt(time[0]) * NANOS_PER_SECOND + BigInt(time[1])).toString();
}

/** Wall-clock now, which is what a log record's timestamp means. */
export function nowNanos(): string {
  return epochMillisToNanos(Date.now());
}
