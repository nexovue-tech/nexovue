import type { PlotRows } from "@/shared/components/TrendChart";
import { formatDuration } from "@/shared/format/duration";
import { formatPercent, UNKNOWN } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";

import type {
  FindingChanged,
  FindingDetail,
  FindingSignal,
  FindingSignalKind,
  NewNormalPreview,
  SignalSeries,
} from "./api";

/**
 * What a finding's page says and offers (FR31, design *The finding page*), apart from
 * how it is drawn, so each rule can be read and tested on its own.
 */

const MS_PER_SECOND = 1000;
const PERCENT = 100;

export const SIGNAL_NAMES: Record<FindingSignalKind, string> = {
  slower: "Slower",
  failing: "Failing more",
  traffic: "Losing traffic",
};

/** A value in its signal's unit; a missing one is the em dash, never a zero. */
export function formatSignalValue(unit: FindingSignal["unit"], value: number | null): string {
  if (value === null) return UNKNOWN;
  if (unit === "ms") return formatDuration(value);
  if (unit === "%") return formatPercent(value / PERCENT);
  return formatRatePerMinute(value);
}

/** "412 ms at worst, against 110 ms normally. Now 330 ms." */
export function describeSignal(entry: FindingSignal): string {
  const value = (reading: number | null) => formatSignalValue(entry.unit, reading);
  return `${value(entry.worst)} at worst, against ${value(entry.normal)} normally. Now ${value(entry.current)}.`;
}

/** Who may do what to this finding, as this person (FR23, AC39). */
export interface FindingActions {
  acknowledge: boolean;
  falseAlarm: boolean;
  newNormal: boolean;
  mute: boolean;
}

export function availableActions(
  finding: FindingDetail,
  { canOperate, canConfigure }: { canOperate: boolean; canConfigure: boolean },
): FindingActions {
  const open = finding.state !== "closed";
  const ofAService = finding.service_name !== null;
  return {
    acknowledge: canOperate && finding.state === "open",
    // A closed finding keeps only this, and only if it recovered: it may still have
    // been nothing (design, *closed*).
    falseAlarm: canOperate && (open || finding.closed_reason === "recovered"),
    newNormal: canConfigure && open && ofAService,
    mute: canConfigure && open && ofAService,
  };
}

/**
 * The dangerous action states its effect in real numbers before it can be confirmed:
 * "checkout's normal becomes about 330 ms, from 110 ms."
 */
export function newNormalSentence(service: string, preview: NewNormalPreview): string {
  const parts = preview.signals.map((shift) => {
    const after = formatSignalValue(shift.unit, shift.normal_after);
    const before = formatSignalValue(shift.unit, shift.normal_before);
    const what = shift.signal === "traffic" ? "normal traffic" : "normal";
    return `${service}'s ${what} becomes about ${after}, from ${before}`;
  });
  return `${parts.join("; ")}.`;
}

/** Why a new normal cannot be set yet: the level is not known well enough. */
export function notYetSentence(preview: NewNormalPreview): string {
  return `Not yet: the new level is known from ${preview.readings} of the ${preview.needed} readings it needs. Try again in a few minutes.`;
}

export type DoneAction = "acknowledge" | "false-alarm" | "new-normal";

/** What happened, and where it went (design principle 3, *Success*). */
export function successSentence(
  action: DoneAction,
  changed: FindingChanged,
  { wasOpen }: { wasOpen: boolean },
): string {
  if (action === "acknowledge") return "Acknowledged. It stays open until it recovers.";
  if (!wasOpen) return "Marked a false alarm. It had already closed, so no message was sent.";
  const closed =
    action === "false-alarm"
      ? "Closed as a false alarm."
      : "Closed as the new normal. Judging goes on at the new level.";
  const told =
    changed.resolved_to.length > 0
      ? `Resolved message sent to ${changed.resolved_to.join(", ")}.`
      : "No channel had been told of it, so no message was sent.";
  return `${closed} ${told}`;
}

/** One signal's chart: the readings, the normal, and the band's edge, on one scale. */
export interface SignalPlot {
  signal: FindingSignalKind;
  unit: FindingSignal["unit"];
  data: PlotRows;
}

export const PLOT_LINES = ["This finding", "Normal", "Edge of the normal band"] as const;

export function signalPlot(series: SignalSeries): SignalPlot {
  return {
    signal: series.signal,
    unit: series.unit,
    data: [
      series.points.map((point) => Date.parse(point.at) / MS_PER_SECOND),
      series.points.map((point) => point.value),
      series.points.map((point) => point.normal),
      series.points.map((point) => point.edge),
    ],
  };
}
