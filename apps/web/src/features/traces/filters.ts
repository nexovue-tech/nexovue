/**
 * What narrows the trace list, held in the URL.
 *
 * The URL is the state, not a copy of it. A person who has found the one slow
 * checkout among two million traces has done real work, and the way they hand
 * that to a colleague is the address bar — so every filter round-trips through
 * the query string and nothing lives only in component state.
 *
 * Absent means "not filtering". A blank string in the URL is the same as no
 * key at all, and is dropped rather than sent, because `?service=` reads as a
 * filter and is not one.
 */

/** The stored spellings. Storage holds `ERROR`, not `STATUS_CODE_ERROR`. */
export const TRACE_STATUSES = ["ERROR", "OK"] as const;
export type TraceStatusFilter = (typeof TRACE_STATUSES)[number];

export interface TraceFilters {
  service?: string;
  operation?: string;
  status?: TraceStatusFilter;
  minDurationMs?: number;
  maxDurationMs?: number;
  /** The attribute name, such as `db.system`. Unbounded — never a dropdown. */
  attr?: string;
  attrValue?: string;
}

export const NO_FILTERS: TraceFilters = {};

/**
 * Read the URL's own search object into filters.
 *
 * Anything unrecognised is dropped rather than passed on: a hand-edited or
 * stale link should show the traces it can, not a 422 the reader cannot act on.
 */
export function parseTraceFilters(search: Record<string, unknown>): TraceFilters {
  const filters: TraceFilters = {
    service: text(search.service),
    operation: text(search.operation),
    status: status(search.status),
    minDurationMs: duration(search.min_duration_ms),
    maxDurationMs: duration(search.max_duration_ms),
    attr: text(search.attr),
    attrValue: text(search.attr_value),
  };

  // A path with nothing to compare against filters nothing, and the API
  // refuses it. Dropping it here keeps a half-typed filter off the wire.
  if (filters.attr !== undefined && filters.attrValue === undefined) {
    filters.attr = undefined;
  }

  return stripUndefined(filters);
}

/** The query string these filters travel as. Keys match the API's own names. */
export function toSearchParams(filters: TraceFilters): Record<string, string | number> {
  return stripUndefined({
    service: filters.service,
    operation: filters.operation,
    status: filters.status,
    min_duration_ms: filters.minDurationMs,
    max_duration_ms: filters.maxDurationMs,
    attr: filters.attr,
    attr_value: filters.attrValue,
  }) as Record<string, string | number>;
}

export function hasAnyFilter(filters: TraceFilters): boolean {
  return Object.keys(toSearchParams(filters)).length > 0;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

function status(value: unknown): TraceStatusFilter | undefined {
  const named = text(value)?.toUpperCase();
  return TRACE_STATUSES.find((candidate) => candidate === named);
}

/** A duration is a non-negative number of milliseconds; nothing else is one. */
function duration(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : Number(text(value));
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

function stripUndefined<T extends object>(value: T): T {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== undefined)) as T;
}

/* ------------------------------------------------------- the empty result */

/**
 * One active filter, named so it can be shown and removed.
 *
 * `key` is what `clearFilter` removes; `label` is what the reader recognises.
 */
export interface ActiveFilter {
  key: keyof TraceFilters;
  label: string;
  value: string;
}

export function activeFilters(filters: TraceFilters): ActiveFilter[] {
  const active: ActiveFilter[] = [];
  if (filters.service) active.push({ key: "service", label: "Service", value: filters.service });
  if (filters.operation)
    active.push({ key: "operation", label: "Operation", value: filters.operation });
  if (filters.status)
    active.push({
      key: "status",
      label: "Status",
      value: filters.status === "ERROR" ? "failed" : "no failures",
    });
  if (filters.minDurationMs !== undefined)
    active.push({
      key: "minDurationMs",
      label: "Slower than",
      value: `${filters.minDurationMs} ms`,
    });
  if (filters.maxDurationMs !== undefined)
    active.push({
      key: "maxDurationMs",
      label: "Faster than",
      value: `${filters.maxDurationMs} ms`,
    });
  if (filters.attr)
    active.push({ key: "attr", label: filters.attr, value: filters.attrValue ?? "" });
  return active;
}

/** Removing one filter. The attribute is a pair, so both halves go together. */
export function clearFilter(filters: TraceFilters, key: keyof TraceFilters): TraceFilters {
  const next = { ...filters, [key]: undefined };
  if (key === "attr") next.attrValue = undefined;
  return stripUndefined(next);
}

/**
 * Why nothing came back, in words the reader can act on.
 *
 * The facets are the services and operations the window actually holds, counted
 * with no filters applied — so this can tell three different situations apart
 * instead of saying "no traces" to all of them. The commonest real cause is the
 * third: a filter naming something that was busy an hour ago and silent now.
 */
export function explainEmptyResult(
  filters: TraceFilters,
  windowLabel: string,
  known: { services: readonly string[]; operations: readonly string[] },
): string {
  if (known.services.length === 0) {
    return `Nothing has been assembled in ${windowLabel}. Widen the time range, or check that your services are still exporting.`;
  }

  const unknown = namedButAbsent(filters, known);
  if (unknown) {
    return `No ${unknown.kind} called “${unknown.name}” sent anything in ${windowLabel}. ${known.services.length} ${known.services.length === 1 ? "service is" : "services are"} reporting — pick one of those, or widen the time range.`;
  }

  const applied = activeFilters(filters);
  if (applied.length === 0) {
    return `Nothing has been assembled in ${windowLabel}. Widen the time range, or check that your services are still exporting.`;
  }

  return `Traces were assembled in ${windowLabel}, but none match ${listFilters(applied)}. Drop a filter, or widen the time range.`;
}

function namedButAbsent(
  filters: TraceFilters,
  known: { services: readonly string[]; operations: readonly string[] },
): { kind: string; name: string } | undefined {
  if (filters.service && !known.services.includes(filters.service)) {
    return { kind: "service", name: filters.service };
  }
  if (filters.operation && !known.operations.includes(filters.operation)) {
    return { kind: "operation", name: filters.operation };
  }
  return undefined;
}

function listFilters(applied: readonly ActiveFilter[]): string {
  const phrases = applied.map((filter) => `${filter.label.toLowerCase()} ${filter.value}`);
  if (phrases.length === 1) return phrases[0];
  return `${phrases.slice(0, -1).join(", ")} and ${phrases[phrases.length - 1]}`;
}
