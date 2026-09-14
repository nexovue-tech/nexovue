/**
 * The one flat list the screen renders.
 *
 * Services and the endpoints opened underneath them share a single row array so the
 * virtualizer has one index space to work in.
 *
 * The ordering itself is not here and never was a presentation choice: the API
 * returns the first hundred rows of whichever order it was asked for, so a sort
 * applied to that page could only rearrange the rows that survived it.
 */

import { formatCount } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";

import type { EndpointGroup, EndpointHealth, ServiceHealth } from "./api";
import type { ServiceOrder } from "./filters";

/**
 * What the ranking in force is called, for the sentence under the title.
 *
 * The ordering itself is the server's — `LIMIT` cuts whichever order the
 * statement used, so sorting a page here can only rearrange what survived it.
 */
export function orderCaption(order: ServiceOrder): string {
  return order === "slowest" ? "slowest first" : "worst first";
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
  expanded: ReadonlySet<string>,
  endpointsByService: ReadonlyMap<string, EndpointGroup>,
): ServiceRow[] {
  const rows: ServiceRow[] = [];

  for (const service of services) {
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

    for (const endpoint of group?.endpoints ?? []) {
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
  return `${count} ${plural(count, "service")} reporting in ${windowLabel}, ${orderCaption(order)}.`;
}

/** The tooltip behind the rate column — the absolute volume the rate came from. */
export function describeVolume(requestCount: number): string {
  return `${formatCount(requestCount)} ${plural(requestCount, "request")} in this window`;
}
