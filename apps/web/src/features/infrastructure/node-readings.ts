/**
 * The fleet list's numbers, and the one rule the whole screen turns on:
 * **an absent measurement is a rule, never a zero.**
 *
 * A collector runs the scrapers it was configured with. A host that sends CPU and no
 * memory is ordinary, and "0 B" in the memory column would report it as a machine using
 * no memory — a claim we have no reading for. Every formatter here answers `null` with
 * an em dash, and every em dash has a tooltip saying which of the two reasons it is:
 * the node sent no such metric, or it sent one whose readings we could not interpret.
 *
 * The same rule governs a node that went quiet: its row keeps the readings it sent,
 * marked as the node's last known state rather than its current one. Erasing them would
 * be the same lie in a different place.
 */

import { formatBinaryBytes } from "@/shared/format/bytes";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent, isReading, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { formatRelativeTime } from "@/shared/format/time";

import type { NodeSummary } from "./api";

const MS_PER_SECOND = 1000;

/** Below this a rate rounds to "0 B/s", which reads as "no traffic". */
const SMALLEST_SHOWN_RATE = 1;

/**
 * Binary prefixes throughout, for memory and for the rate columns alike. Switching to
 * decimal for one of them would put two meanings of "G" on the same row.
 */
export function formatByteRate(bytesPerSecond: number | null): string {
  if (!isReading(bytesPerSecond)) return UNKNOWN;
  // A trickle is not silence, and rounding it to zero would say it was.
  if (bytesPerSecond > 0 && bytesPerSecond < SMALLEST_SHOWN_RATE) return "<1 B/s";
  return `${formatBinaryBytes(bytesPerSecond)}/s`;
}

export function formatCores(cores: number | null): string {
  if (!isReading(cores)) return UNKNOWN;
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

/* ------------------------------------------------------- has it stopped reporting? */

/**
 * The label on a node that went quiet.
 *
 * "Stopped reporting" and not "down", because those are different claims and telemetry
 * cannot tell them apart: a machine that was switched off and one whose collector
 * crashed look identical from here. Saying the one we can prove is the point.
 */
export const STOPPED_REPORTING = "stopped reporting";

/**
 * Why a row is marked, in the terms that let a reader judge it: the cadence this node
 * was keeping, and how long it has been since it kept it.
 *
 * The cadence is the whole reason the mark means anything. "Last seen 4 min ago" is
 * unremarkable for a host scraped every ten minutes and three missed reports for one
 * scraped every minute, and only the interval separates the two.
 */
export function explainStale(node: NodeSummary, now = Date.now()): string {
  const silence = `Last reported ${formatRelativeTime(node.last_seen, now)}`;
  if (node.reporting_interval_seconds === null) {
    return `${silence}, and it sent too few points to show how often it reports. It may be down, or it may have stopped sending — this cannot tell the two apart.`;
  }
  return `${silence}, having been reporting every ${formatDuration(
    node.reporting_interval_seconds * MS_PER_SECOND,
  )}. It may be down, or it may have stopped sending — this cannot tell the two apart.`;
}

/**
 * The fleet-wide count, or null when everything is still reporting.
 *
 * Said above the table for the same reason the unattributed notice is: a node that
 * vanished is the most important thing this screen can tell someone, and a mark on a row
 * far down a virtualized list is not something a reader will find by scrolling.
 */
export function describeStale(nodes: readonly NodeSummary[]): string | null {
  const quiet = nodes.filter((candidate) => candidate.stale);
  if (quiet.length === 0) return null;

  const named = quiet.map(nodeName).join(", ");
  const subject = quiet.length === 1 ? "node has" : `nodes have`;
  return `${quiet.length} ${subject} stopped reporting: ${named}. Each is still listed with the readings it sent — they are that node's last known state, not its current one. A node that vanished may be down, or may only have stopped sending; nothing in the telemetry separates those.`;
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

/** Memory as used-over-total, which is how the question is actually asked. */
export function describeMemory(node: NodeSummary): string {
  if (node.memory_used_bytes === null && node.memory_limit_bytes === null) {
    return explainMissing(node, MEMORY_METRICS);
  }
  return `${formatBinaryBytes(node.memory_used_bytes)} used of ${formatBinaryBytes(node.memory_limit_bytes)}.`;
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

/* ------------------------------------------------------- saturation and errors */

/**
 * A node is saturated when work is waiting, not when it is busy.
 *
 * The two are different questions and USE asks both. A disk at any utilization
 * with operations queued behind it is slower than its own numbers suggest, and
 * a host swapping is out of memory whatever `memory_utilization` says.
 */
const QUEUE_WORTH_SAYING = 1;
const SWAP_WORTH_SAYING = 0.5;

export function isSaturated(node: NodeSummary): boolean {
  return (
    (node.disk_queue_length !== null && node.disk_queue_length >= QUEUE_WORTH_SAYING) ||
    (node.swap_utilization !== null && node.swap_utilization >= SWAP_WORTH_SAYING)
  );
}

/**
 * The fleet's saturation, said once above the table.
 *
 * Named rather than counted: "2 nodes" sends a reader scanning eleven rows for
 * two numbers they have not been told the shape of. The threshold is in the
 * sentence because a queue of one is not an incident and a reader deciding
 * whether to care needs to know what we called saturated.
 */
export function describeSaturation(nodes: readonly NodeSummary[]): string | null {
  const saturated = nodes.filter(isSaturated);
  if (saturated.length === 0) return null;

  // Counted first, named after — the same shape as `describeStale`. A node's
  // name here can be a phrase ("reported by recommendation"), and leading with
  // one of those makes the sentence read as prose rather than as a list.
  const named = saturated.map(nodeName).join(", ");
  const subject = saturated.length === 1 ? "node has" : "nodes have";
  return `${saturated.length} ${subject} work waiting: ${named}. Saturation is not the same as being busy — a disk with operations queued behind it is slower than its own numbers suggest, and a host using swap has run out of memory whatever its memory percentage says.`;
}

/**
 * Network faults over the window, said once.
 *
 * Almost always zero, which is why this is a sentence rather than a column: a
 * column of zeroes is eleven rows of nothing, and the one row that is not zero
 * is the only thing worth the width.
 */
export function describeNetworkFaults(nodes: readonly NodeSummary[]): string | null {
  const faulty = nodes.filter(
    (node) => (node.network_errors ?? 0) > 0 || (node.network_dropped ?? 0) > 0,
  );
  if (faulty.length === 0) return null;

  const said = faulty.map((node) => {
    const errors = node.network_errors ?? 0;
    const dropped = node.network_dropped ?? 0;
    const parts = [
      errors > 0 ? `${formatCount(errors)} ${plural(errors, "error")}` : "",
      dropped > 0 ? `${formatCount(dropped)} dropped` : "",
    ].filter(Boolean);
    return `${nodeName(node)} (${parts.join(", ")})`;
  });

  const subject = faulty.length === 1 ? "node reported" : "nodes reported";
  return `${faulty.length} ${subject} network faults in this window: ${said.join(" · ")}. An interface erroring is not a queue overflowing, so the two are counted apart.`;
}
