/**
 * The fleet list's numbers, and the one rule the whole screen turns on:
 * **an absent measurement is a rule, never a zero.**
 *
 * A collector runs the scrapers it was configured with. A host that sends CPU and no
 * memory is ordinary, and "0 B" in the memory column would report it as a machine using
 * no memory — a claim we have no reading for. Every formatter here answers `null` with
 * an em dash, and every em dash has a tooltip saying which of the two reasons it is:
 * the node sent no such metric, or it sent one whose readings we could not interpret.
 */

import type { NodeSummary } from "./api";

/** Nothing measured. */
const UNKNOWN = "—";

/** Fixed locale: digit grouping must not change with the reader's machine. */
const NUMBER_LOCALE = "en-US";

/**
 * Binary prefixes throughout, for memory and for rates alike. Memory is quoted in GiB by
 * every host tool there is, and switching to SI for the rate columns would put two
 * meanings of "G" on one row.
 */
const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;
const BYTES_PER_UNIT = 1024;

const PERCENT = 100;
const SMALLEST_SHOWN_PERCENT = 0.1;

/** Below this a rate rounds to "0 B/s", which reads as "no traffic". */
const SMALLEST_SHOWN_RATE = 1;

export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return UNKNOWN;

  let value = bytes;
  let unit = 0;
  while (value >= BYTES_PER_UNIT && unit < BYTE_UNITS.length - 1) {
    value /= BYTES_PER_UNIT;
    unit += 1;
  }

  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${BYTE_UNITS[unit]}`;
}

export function formatByteRate(bytesPerSecond: number | null): string {
  if (bytesPerSecond === null || !Number.isFinite(bytesPerSecond) || bytesPerSecond < 0) {
    return UNKNOWN;
  }
  // A trickle is not silence, and rounding it to zero would say it was.
  if (bytesPerSecond > 0 && bytesPerSecond < SMALLEST_SHOWN_RATE) return "<1 B/s";
  return `${formatBytes(bytesPerSecond)}/s`;
}

/**
 * A fraction on the wire, a percentage on the screen. Deliberately not clamped to 100:
 * a figure above it means the roll-up is wrong, and hiding that is how a wrong number
 * survives.
 */
export function formatPercent(fraction: number | null): string {
  if (fraction === null || !Number.isFinite(fraction) || fraction < 0) return UNKNOWN;

  const percent = fraction * PERCENT;
  if (percent > 0 && percent < SMALLEST_SHOWN_PERCENT) return "<0.1%";
  return `${percent.toFixed(1)}%`;
}

export function formatCores(cores: number | null): string {
  if (cores === null || !Number.isFinite(cores) || cores < 0) return UNKNOWN;
  return `${cores.toFixed(2)} cores`;
}

/**
 * The CPU cell. A host reports a fraction of its CPUs and a container reports cores
 * consumed; those are different quantities, so they carry different units rather than
 * sharing a column that silently means two things.
 */
export function formatCpu(node: NodeSummary): string {
  if (node.kind === "container") return formatCores(node.cpu_cores_used);
  return formatPercent(node.cpu_utilization);
}

/**
 * What a node is called.
 *
 * A resource carrying neither name collapses to one unnamed node rather than being
 * dropped — which is a real state, not a hypothetical: the host metrics in our own store
 * arrived with no `host.name` at all. Saying so is the point; a blank row would look
 * like a rendering bug instead of a collector that needs configuring.
 */
export function nodeName(node: NodeSummary): string {
  if (node.container) return node.container;
  if (node.host) return node.host;
  return "unnamed node";
}

export function isUnnamed(node: NodeSummary): boolean {
  return !node.container && !node.host;
}

export const UNNAMED_NODE_REASON =
  "This node's resource carried no host.name and no container.name, so everything reporting without one is rolled up here. Set the resourcedetection processor on your collector, or a host.name resource attribute on the exporter, to tell these machines apart.";

/**
 * Why a cell is empty — which is two different situations that need different fixes.
 *
 * Either the node never sent the metric, in which case a scraper is switched off, or it
 * sent one and no reading in it could be interpreted, in which case the metric arrived
 * with attributes we do not recognise. Guessing between them would send someone to the
 * wrong configuration file.
 */
export function explainMissing(node: NodeSummary, metricNames: readonly string[]): string {
  const sent = metricNames.filter((name) => node.signals.includes(name));

  if (sent.length === 0) {
    return `Not reported. ${nodeName(node)} sent none of ${metricNames.join(", ")} in this window, so the scraper for it is not enabled.`;
  }
  return `Not measured. ${sent.join(", ")} arrived, but no reading in it could be identified — the metric is being sent with attributes we do not recognise.`;
}

export function formatCount(count: number): string {
  if (!Number.isFinite(count)) return UNKNOWN;
  return Math.round(count).toLocaleString(NUMBER_LOCALE);
}

/** Memory as used-over-total, which is how the question is actually asked. */
export function describeMemory(node: NodeSummary): string {
  if (node.memory_used_bytes === null && node.memory_limit_bytes === null) {
    return explainMissing(node, MEMORY_METRICS);
  }
  return `${formatBytes(node.memory_used_bytes)} used of ${formatBytes(node.memory_limit_bytes)}.`;
}

/* The metric names behind each column, so a tooltip can name what is missing. */
export const CPU_METRICS = ["system.cpu.utilization", "container.cpu.usage"] as const;
export const MEMORY_METRICS = [
  "system.memory.usage",
  "system.memory.utilization",
  "container.memory.usage",
] as const;
export const DISK_METRICS = ["system.disk.io", "container.disk.io"] as const;
export const NETWORK_METRICS = ["system.network.io", "container.network.io"] as const;
export const FILESYSTEM_METRICS = ["system.filesystem.usage", "system.filesystem.limit"] as const;
