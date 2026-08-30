/**
 * The one flat list the screen renders, and the ordering behind it.
 *
 * Services and the endpoints opened underneath them share a single row array so the
 * virtualizer has one index space to work in, and so the ordering rule is applied in
 * exactly one place instead of twice.
 */

import type { EndpointGroup, EndpointHealth, HealthMetrics, ServiceHealth } from "./api";

/**
 * `worst` is the API's own order — errors, then the slow tail, then volume — and it is
 * the product, so it is never recomputed here. `slowest` is the escape hatch for the
 * window where nothing is failing and something is still dragging.
 */
export type ServiceOrder = "worst" | "slowest";

export const ORDER_OPTIONS: ReadonlyArray<{ id: ServiceOrder; label: string }> = [
  { id: "worst", label: "worst" },
  { id: "slowest", label: "slowest" },
];

export function orderCaption(order: ServiceOrder): string {
  return order === "slowest" ? "slowest first" : "worst first";
}

/**
 * Sorted by the slow tail, descending. Array.prototype.sort is stable, so equal p95s
 * keep the ranking the API gave them rather than a random one.
 */
export function applyOrder<Row extends HealthMetrics>(
  rows: readonly Row[],
  order: ServiceOrder,
): readonly Row[] {
  if (order !== "slowest") return rows;
  return [...rows].sort((left, right) => right.p95_ms - left.p95_ms);
}

export type ServiceRow =
  | { kind: "service"; key: string; service: ServiceHealth; expanded: boolean }
  | { kind: "endpoint"; key: string; endpoint: EndpointHealth }
  | { kind: "note"; key: string; serviceName: string; note: "pending" | "empty" | "failed" };

const SERVICE_ROW_HEIGHT = 36;
const ENDPOINT_ROW_HEIGHT = 28;
const NOTE_ROW_HEIGHT = 34;
/** The loading note holds the shape of two endpoint rows, because that is what it is. */
const PENDING_NOTE_HEIGHT = ENDPOINT_ROW_HEIGHT * 2;

export function rowHeight(row: ServiceRow): number {
  if (row.kind === "service") return SERVICE_ROW_HEIGHT;
  if (row.kind === "endpoint") return ENDPOINT_ROW_HEIGHT;
  return row.note === "pending" ? PENDING_NOTE_HEIGHT : NOTE_ROW_HEIGHT;
}

export function serviceRowKey(serviceName: string): string {
  return `service:${serviceName}`;
}

function endpointRowKey(endpoint: EndpointHealth): string {
  return `endpoint:${endpoint.service_name}:${endpoint.span_kind}:${endpoint.endpoint}`;
}

/**
 * Every service, each followed by its endpoints when it is open.
 *
 * An open service with no group yet is still loading — the query is registered a render
 * before its first result exists, and a row that vanishes for one frame is worse than a
 * row that says "loading".
 */
export function buildServiceRows(
  services: readonly ServiceHealth[],
  order: ServiceOrder,
  expanded: ReadonlySet<string>,
  endpointsByService: ReadonlyMap<string, EndpointGroup>,
): ServiceRow[] {
  const rows: ServiceRow[] = [];

  for (const service of applyOrder(services, order)) {
    const isExpanded = expanded.has(service.service_name);
    rows.push({
      kind: "service",
      key: serviceRowKey(service.service_name),
      service,
      expanded: isExpanded,
    });
    if (!isExpanded) continue;

    const group = endpointsByService.get(service.service_name);
    const note = noteFor(group);
    if (note !== undefined) {
      rows.push({
        kind: "note",
        key: `note:${service.service_name}`,
        serviceName: service.service_name,
        note,
      });
      continue;
    }

    for (const endpoint of applyOrder(group?.endpoints ?? [], order)) {
      rows.push({ kind: "endpoint", key: endpointRowKey(endpoint), endpoint });
    }
  }

  return rows;
}

function noteFor(group: EndpointGroup | undefined): "pending" | "empty" | "failed" | undefined {
  if (group === undefined || group.status === "pending") return "pending";
  if (group.status === "error") return "failed";
  return group.endpoints.length === 0 ? "empty" : undefined;
}

/** The sentence under the title. It says what is on the screen and in what order. */
export function describeServices(count: number, windowLabel: string, order: ServiceOrder): string {
  const services = count === 1 ? "service" : "services";
  return `${count} ${services} reporting in the last ${windowLabel}, ${orderCaption(order)}.`;
}
