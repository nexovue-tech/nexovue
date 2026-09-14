import { UNKNOWN } from "@/shared/format/number";

/**
 * Severity, and the one place on this screen colour is allowed.
 *
 * The design rule is that health is the *absence* of colour, and a log screen is
 * where that rule earns its keep: INFO is the overwhelming majority of every real log
 * stream, so tinting it would produce a wall of colour that means nothing and would
 * leave the twelve lines that matter indistinguishable inside it.
 *
 * So INFO and everything below it are neutral — no badge, no tint, no rule. Only WARN
 * and ERROR are coloured, and each is also carried by a left rule and a weight change,
 * because colour is never the only carrier.
 */

/**
 * OTLP severity numbers. The specification defines four numbers per name (TRACE 1–4,
 * DEBUG 5–8, INFO 9–12, WARN 13–16, ERROR 17–20, FATAL 21–24), so a threshold is
 * always the first number of its band.
 */
export const SEVERITY_DEBUG = 5;
export const SEVERITY_INFO = 9;
export const SEVERITY_WARN = 13;
export const SEVERITY_ERROR = 17;
export const SEVERITY_FATAL = 21;

/**
 * The level a line is at, in one spelling.
 *
 * **`severity_text` is whatever the emitter felt like writing.** One project's
 * logs carried "INFO", "info" and "Information" for the same OTLP number — three
 * spellings of one level in a list people scan by level — and 117,127 lines
 * carried number 9 with no text at all, so the cell was blank about a level the
 * record knew. "Information" also overflowed its column, because the column is
 * sized for a level and that is a sentence.
 *
 * The number is the specification's own, so it is what the column reads from. The
 * emitter's own wording is not thrown away: it is in the expanded line, where it
 * is evidence rather than a label.
 */
const BANDS: ReadonlyArray<{ floor: number; label: string }> = [
  { floor: SEVERITY_FATAL, label: "FATAL" },
  { floor: SEVERITY_ERROR, label: "ERROR" },
  { floor: SEVERITY_WARN, label: "WARN" },
  { floor: SEVERITY_INFO, label: "INFO" },
  { floor: SEVERITY_DEBUG, label: "DEBUG" },
  { floor: 1, label: "TRACE" },
];

export function severityLabel(severityNumber: number): string {
  // Zero is OTLP's "unspecified", which is a reading nobody sent rather than a
  // level below TRACE — an em dash, like every other absent reading here.
  return BANDS.find((band) => severityNumber >= band.floor)?.label ?? UNKNOWN;
}

/** Whether the emitter's own wording adds anything the label does not already say. */
export function emitterSaidSomethingElse(line: { severity: string; severity_number: number }) {
  const written = line.severity.trim();
  return written !== "" && written.toUpperCase() !== severityLabel(line.severity_number);
}

export type SeverityTone = "critical" | "warning" | "quiet";

export function severityTone(severityNumber: number): SeverityTone {
  if (severityNumber >= SEVERITY_WARN && severityNumber < SEVERITY_ERROR) return "warning";
  if (severityNumber >= SEVERITY_ERROR) return "critical";
  return "quiet";
}

interface ToneStyle {
  /** The severity word itself. */
  label: string;
  /** The left rule on the row — the non-colour carrier of the same fact. */
  rule: string;
  /** The body text. Louder for a failure, ordinary for everything else. */
  body: string;
}

const TONE_STYLES: Record<SeverityTone, ToneStyle> = {
  critical: {
    label: "text-critical-text",
    rule: "border-l-critical-solid",
    body: "text-ink",
  },
  warning: {
    label: "text-warning-text",
    rule: "border-l-warning-solid",
    body: "text-ink",
  },
  quiet: {
    label: "text-ink-dim",
    rule: "border-l-transparent",
    body: "text-ink-dim",
  },
};

export function severityStyle(severityNumber: number): ToneStyle {
  return TONE_STYLES[severityTone(severityNumber)];
}

export interface SeverityFilter {
  id: string;
  label: string;
  /** Undefined means no floor at all, which is not the same as a floor of zero. */
  minSeverity?: number;
}

/**
 * The floors worth offering. Ranked loudest-last so the control reads as a dial from
 * "everything" to "only what is broken", which is the direction someone turns it.
 */
export const SEVERITY_FILTERS: readonly SeverityFilter[] = [
  { id: "all", label: "All" },
  { id: "debug", label: "Debug+", minSeverity: SEVERITY_DEBUG },
  { id: "info", label: "Info+", minSeverity: SEVERITY_INFO },
  { id: "warn", label: "Warn+", minSeverity: SEVERITY_WARN },
  { id: "error", label: "Error+", minSeverity: SEVERITY_ERROR },
];

export function severityFilterById(id: string): SeverityFilter {
  return SEVERITY_FILTERS.find((filter) => filter.id === id) ?? SEVERITY_FILTERS[0];
}
