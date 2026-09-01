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
 *
 * A reporter reports the host's fraction — it is a machine's readings arriving without a
 * machine's name, not a different measurement — so it reads as a host does here.
 */
export function formatCpu(node: NodeSummary): string {
  if (node.kind === "container") return formatCores(node.cpu_cores_used);
  return formatPercent(node.cpu_utilization);
}

/** The fraction the CPU bar draws, under the same rule `formatCpu` uses. */
export function cpuFraction(node: NodeSummary): number | null {
  return node.kind === "container" ? null : node.cpu_utilization;
}

/**
 * What a node is called.
 *
 * A resource that named neither a host nor a container is labelled by *who reported* it,
 * never by where it ran: `service.instance.id` identifies a process, and naming a machine
 * after one would invent a host nobody told us about. That is the state the host metrics
 * in our own store are in — every one of them.
 */
export function nodeName(node: NodeSummary): string {
  if (node.container) return node.container;
  if (node.host) return node.host;
  if (node.reporter) return `reported by ${node.reporter}`;
  return "an unidentified reporter";
}

/** How to name a node inside a sentence, where the display label reads wrong. */
function nodeSubject(node: NodeSummary): string {
  return node.container || node.host || node.reporter || "This node";
}

/** Readings that arrived with no machine to attach them to. */
export function isUnattributed(node: NodeSummary): boolean {
  return node.kind === "reporter";
}

/**
 * The one-line fix, and the reason these rows explain themselves at all: a customer who
 * sets `host.name` once never sees a reporter row again.
 *
 * `host.name` is OpenTelemetry's resource attribute for the machine — Recommended, and
 * still marked development. `OTEL_RESOURCE_ATTRIBUTES` is read by every SDK, so that
 * spelling works everywhere; the Python SDK, which is what reports unattributed here,
 * can also detect it for itself.
 */
export const HOST_NAME_REMEDY =
  "To fix it: set host.name on the resource — OTEL_RESOURCE_ATTRIBUTES=host.name=$(hostname) works in every SDK, the OpenTelemetry Python SDK detects it for itself with OTEL_EXPERIMENTAL_RESOURCE_DETECTORS=host, and a collector scraping on the machine's behalf sets it with the resourcedetection processor's system detector.";

/** Why one row is named after a process, and what it costs to leave it that way. */
export function explainUnattributed(node: NodeSummary): string {
  const instance = node.reporter_instance ? ` (service.instance.id ${node.reporter_instance})` : "";
  return `${nodeSubject(node)}${instance} is reporting host metrics without setting host.name, so these readings cannot be attributed to a machine. ${HOST_NAME_REMEDY}`;
}

/**
 * The fleet-wide version of the same thing, or null when every node named itself.
 *
 * Said once above the table rather than only in a tooltip, because it is one setting on
 * one resource and the reader has to see it to act on it. It also states the cost of the
 * grouping, which is not something a reader could infer from the rows.
 */
export function describeUnattributed(nodes: readonly NodeSummary[]): string | null {
  const unattributed = nodes.filter(isUnattributed);
  if (unattributed.length === 0) return null;

  const reporters = [...new Set(unattributed.map((node) => node.reporter).filter(Boolean))].sort();
  const subject =
    reporters.length === 0
      ? "Some resources are"
      : `${reporters.join(", ")} ${reporters.length === 1 ? "is" : "are"}`;

  return `${subject} reporting host metrics without setting host.name, so these readings cannot be attributed to a machine. They are grouped by the process that sent them instead, which keeps each row's numbers coherent and over-counts the fleet: three instrumented services on one machine appear as three rows. ${HOST_NAME_REMEDY}`;
}

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
    return `Not reported. ${nodeSubject(node)} sent none of ${metricNames.join(", ")} in this window, so the scraper for it is not enabled.`;
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
