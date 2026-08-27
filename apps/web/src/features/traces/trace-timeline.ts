import type { Span } from "./api";

/**
 * The one horizontal scale every bar is drawn against.
 *
 * It is derived from the spans themselves, not from the trace envelope's start and
 * duration. Clocks on different machines disagree, so a child can legitimately start
 * before its parent or end after it. Measuring the window from the extremes of what
 * actually arrived means every bar fits, and the skew stays visible instead of being
 * clipped away.
 */

export interface Timeline {
  startMs: number;
  endMs: number;
  spanMs: number;
}

export interface Placement {
  offsetPercent: number;
  widthPercent: number;
}

/** A span too short to see is still a span; it gets a mark rather than nothing. */
const MINIMUM_WIDTH_PERCENT = 0.5;
const FULL_WIDTH_PERCENT = 100;

const EMPTY_TIMELINE: Timeline = { startMs: 0, endMs: 0, spanMs: 0 };

export function toEpochMs(iso: string): number {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function spanStartMs(span: Span): number {
  return toEpochMs(span.start_time);
}

export function spanEndMs(span: Span): number {
  return spanStartMs(span) + Math.max(0, span.duration_ms);
}

export function buildTimeline(spans: readonly Span[]): Timeline {
  if (spans.length === 0) return EMPTY_TIMELINE;

  let startMs = Number.POSITIVE_INFINITY;
  let endMs = Number.NEGATIVE_INFINITY;

  for (const span of spans) {
    startMs = Math.min(startMs, spanStartMs(span));
    endMs = Math.max(endMs, spanEndMs(span));
  }

  return { startMs, endMs, spanMs: Math.max(0, endMs - startMs) };
}

/**
 * A trace of one instantaneous span has no width to divide by. Rather than a zero-width
 * bar on an invisible scale, the single span becomes the whole window — which is the
 * truth: it is the entire trace.
 */
export function placeSpan(span: Span, timeline: Timeline): Placement {
  if (timeline.spanMs <= 0) return { offsetPercent: 0, widthPercent: FULL_WIDTH_PERCENT };

  const offset = ((spanStartMs(span) - timeline.startMs) / timeline.spanMs) * FULL_WIDTH_PERCENT;
  const width = (Math.max(0, span.duration_ms) / timeline.spanMs) * FULL_WIDTH_PERCENT;

  const clampedWidth = Math.min(FULL_WIDTH_PERCENT, Math.max(MINIMUM_WIDTH_PERCENT, width));
  const clampedOffset = Math.min(
    FULL_WIDTH_PERCENT - clampedWidth,
    Math.max(0, Number.isFinite(offset) ? offset : 0),
  );

  return { offsetPercent: clampedOffset, widthPercent: clampedWidth };
}

/** Evenly spaced gridlines, so a bar's position reads as a time and not just a place. */
export function timelineTicks(
  timeline: Timeline,
  count: number,
): Array<{ percent: number; atMs: number }> {
  return Array.from({ length: count + 1 }, (_, step) => ({
    percent: (step / count) * FULL_WIDTH_PERCENT,
    atMs: (timeline.spanMs * step) / count,
  }));
}
