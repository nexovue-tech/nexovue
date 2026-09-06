/**
 * crypto.getRandomValues is the only Crypto member available outside a secure
 * context, so ids work on a plain-HTTP development origin. crypto.randomUUID
 * would not, and would need a second code path for the same job.
 */

const TRACE_ID_BYTES = 16;
const SPAN_ID_BYTES = 8;
const SESSION_ID_BYTES = 16;

export function randomnessAvailable(): boolean {
  return typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function";
}

function randomHex(byteLength: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(byteLength));
  let hex = "";
  for (const byte of bytes) hex += byte.toString(16).padStart(2, "0");
  return hex;
}

export function newTraceId(): string {
  return randomHex(TRACE_ID_BYTES);
}

export function newSpanId(): string {
  return randomHex(SPAN_ID_BYTES);
}

export function newSessionId(): string {
  return randomHex(SESSION_ID_BYTES);
}
