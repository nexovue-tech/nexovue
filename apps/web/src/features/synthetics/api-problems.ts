import { ApiError } from "@/shared/api-client";

/**
 * What a refused save said, in the two shapes a form shows: a sentence, and a message
 * per field.
 *
 * No status is read here — `app/query-client.ts` owns what a status means. A refusal
 * that names fields carries them in the RFC 9457 `errors` extension, and its presence
 * is the whole signal.
 */

/** Pydantic locates a body field as `body.config…`; the form knows it as `config…`. */
const LOCATION_PREFIX = /^(body|query|path)\./;

interface FieldProblem {
  field: string;
  reason: string;
}

function isFieldProblem(value: unknown): value is FieldProblem {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.field === "string" && typeof candidate.reason === "string";
}

export function fieldProblemsOf(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  const reported = error.problem?.errors;
  if (!Array.isArray(reported)) return {};
  const byField: Record<string, string> = {};
  for (const item of reported) {
    if (!isFieldProblem(item)) continue;
    const field = item.field.replace(LOCATION_PREFIX, "");
    if (!(field in byField)) byField[field] = item.reason;
  }
  return byField;
}

export function problemMessageOf(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : undefined;
  return error.problem?.detail;
}
