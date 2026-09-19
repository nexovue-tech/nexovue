/**
 * Profile types in people's words, and values in their units.
 *
 * The source names types as it pleases — "cpu", "alloc_space", "inuse_objects" — so
 * the known ones are named for people and an unknown one is shown as sent, never
 * guessed at.
 */

const TYPE_NAMES: Record<string, string> = {
  cpu: "CPU time",
  samples: "CPU samples",
  wall: "Wall time",
  off_cpu: "Off-CPU time",
  alloc_space: "Allocated memory",
  alloc_objects: "Allocations",
  allocated_space: "Allocated memory",
  allocated_objects: "Allocations",
  inuse_space: "Memory in use",
  inuse_objects: "Objects in use",
  contentions: "Lock contention",
  delay: "Lock wait time",
  goroutine: "Goroutines",
  goroutines: "Goroutines",
  exceptions: "Exceptions",
};

export function typeName(sampleType: string): string {
  return TYPE_NAMES[sampleType] ?? sampleType;
}

const NANOS_PER_MS = 1_000_000;

/** A value in its unit, rounded for reading. An unknown unit is shown as sent. */
export function formatValue(value: number, unit: string): string {
  switch (unit) {
    case "nanoseconds":
      return formatDuration(value / NANOS_PER_MS);
    case "microseconds":
      return formatDuration(value / 1_000);
    case "milliseconds":
      return formatDuration(value);
    case "bytes":
      return formatBytes(value);
    case "count":
    case "":
      return value.toLocaleString("en");
    default:
      return `${value.toLocaleString("en")} ${unit}`;
  }
}

function formatDuration(ms: number): string {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)} min`;
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)} s`;
  return `${Math.round(ms).toLocaleString("en")} ms`;
}

function formatBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${index === 0 ? value : value.toFixed(1)} ${units[index]}`;
}

/** A share of the whole, as a percentage people read: one decimal under 10 %. */
export function formatShare(value: number, total: number): string {
  if (total <= 0) return "—";
  const percent = (value / total) * 100;
  return percent < 10 ? `${percent.toFixed(1)} %` : `${Math.round(percent)} %`;
}

const UNIT_SHORT: Record<string, string> = {
  nanoseconds: "ns",
  microseconds: "µs",
  milliseconds: "ms",
};

/** A type as the picker lists it: what it measures, and in what — "Memory in use (bytes)". */
export function typeLabel(sampleType: string, unit: string): string {
  const shown = UNIT_SHORT[unit] ?? unit;
  return shown ? `${typeName(sampleType)} (${shown})` : typeName(sampleType);
}

/** A type's name inside a sentence: "memory in use", but "CPU time" keeps its acronym. */
export function typePhrase(sampleType: string): string {
  const name = typeName(sampleType);
  const acronym = name.length > 1 && name[1] === name[1].toUpperCase() && name[1] !== " ";
  return acronym ? name : name.charAt(0).toLowerCase() + name.slice(1);
}
