import { EdgeLabelRenderer, type EdgeProps, getSmoothStepPath } from "@xyflow/react";

import { formatDuration } from "@/shared/format/duration";

import { formatErrorRate, formatRate } from "./format";
import type { DependencyEdge as DependencyEdgeShape, Health } from "./service-graph";

/**
 * One dependency: an arrow, and the three numbers that say whether it is in trouble.
 *
 * The numbers are on the map, not in a tooltip. An edge with errors is what someone
 * opens this screen to find, and a finding that needs hovering has not been found.
 *
 * Three things are encoded, none of them by colour alone:
 *   - **rate** by stroke width, on a log scale
 *   - **kind** by dash — a queue hop can be retried for hours, a call cannot
 *   - **failure** by severity colour *and* a badge carrying the count
 */

const EDGE_CORNER_RADIUS = 4;
const THINNEST_STROKE = 1;
const THICKEST_STROKE = 3.25;
const QUEUE_DASH = "6 4";

/** How far the self-call loop reaches past the node's edges, and how high it rides. */
const LOOP_REACH = 28;
const LOOP_RISE = 46;

const ARROW_LENGTH = 8;
const ARROW_HALF_WIDTH = 4;

const STROKE: Record<Health, string> = {
  critical: "stroke-critical-solid",
  warning: "stroke-warning-solid",
  healthy: "stroke-line-strong",
};

const ARROWHEAD: Record<Health, string> = {
  critical: "fill-critical-solid",
  warning: "fill-warning-solid",
  healthy: "fill-line-strong",
};

/** Badge anatomy from the design system: subtle fill, coloured label, border. */
const BADGE: Record<Health, string> = {
  critical: "bg-critical-subtle text-critical-text border-critical-border",
  warning: "bg-warning-subtle text-warning-text border-warning-border",
  healthy: "",
};

export function DependencyEdge({
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  data,
}: EdgeProps<DependencyEdgeShape>) {
  if (!data) return null;

  const geometry = data.selfCall
    ? selfCallGeometry(sourceX, sourceY, targetX, targetY)
    : throughGeometry(
        { sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition },
        data.laneOffset,
      );

  const health = data.health;
  // What the latency measures differs by kind, so the label says which. On a
  // queue it is the publish — how long sending took — because the delivery lag
  // would have to be measured across two machines' clocks.
  const latencyLabel = data.queued ? "publish p95" : "p95";
  const summary = [
    `${formatRate(data.callsPerMinute)}/min`,
    `${latencyLabel} ${formatDuration(data.p95Ms)}`,
    data.errorCount > 0 ? `${data.errorCount} failed (${formatErrorRate(data.errorRate)})` : "",
    data.queued ? "queued" : "direct call",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      <path
        className={`${STROKE[health]} fill-none`}
        d={geometry.path}
        strokeWidth={strokeWidthFor(data.weight)}
        strokeDasharray={data.queued ? QUEUE_DASH : undefined}
        strokeLinecap="round"
      />
      {/* Every arrow enters its target through the left handle, travelling right, so
          one orientation covers the whole map and no marker definitions are needed. */}
      <path
        className={ARROWHEAD[health]}
        d={`M ${targetX} ${targetY} l ${-ARROW_LENGTH} ${-ARROW_HALF_WIDTH} l 0 ${ARROW_HALF_WIDTH * 2} z`}
      />

      <EdgeLabelRenderer>
        {/* The background is on the container, not on the lines inside it: a gap
            between two backgrounds is a stripe of whatever the label is standing on,
            and on this screen that is an edge running through the text. */}
        <div
          className="pointer-events-auto absolute flex flex-col items-center gap-0.5 rounded-xs bg-page px-1 py-0.5"
          style={{
            transform: `translate(-50%, -50%) translate(${geometry.labelX}px, ${geometry.labelY}px)`,
          }}
          title={summary}
        >
          {/* Stacked, not strung out: the label has to live in the gap between two
              columns, and a wide one forces the whole map to be zoomed out to fit. */}
          <span className="text-center font-mono text-2xs text-ink-dim leading-tight">
            {formatRate(data.callsPerMinute)}/min
            <br />
            {latencyLabel} {formatDuration(data.p95Ms)}
          </span>
          {data.errorCount > 0 ? (
            <span
              className={`rounded-xs border px-1 font-medium font-mono text-2xs ${BADGE[health]}`}
            >
              {data.errorCount} failed · {formatErrorRate(data.errorRate)}
            </span>
          ) : null}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

function strokeWidthFor(weight: number): number {
  return THINNEST_STROKE + (THICKEST_STROKE - THINNEST_STROKE) * weight;
}

interface EdgeGeometry {
  path: string;
  labelX: number;
  labelY: number;
}

/**
 * `laneOffset` moves where the line makes its vertical step, and the label rides with
 * it. Left at the default every edge between the same two columns steps at the same x,
 * which piles their labels into one vertical corridor with a line through each.
 */
function throughGeometry(
  parameters: Parameters<typeof getSmoothStepPath>[0],
  laneOffset: number,
): EdgeGeometry {
  const tip = parameters.targetX - ARROW_LENGTH;
  const [path, labelX, labelY] = getSmoothStepPath({
    ...parameters,
    borderRadius: EDGE_CORNER_RADIUS,
    // The arrowhead is drawn at the handle, so the line stops short of it.
    targetX: tip,
    centerX: (parameters.sourceX + tip) / 2 + laneOffset,
  });
  return { path, labelX, labelY };
}

/**
 * A service calling itself — `loadgen → loadgen` is real, not a data fault.
 *
 * Both handles belong to the same node, so there is no path *between* them. The loop
 * leaves the right edge, turns up, runs back across above the node and drops into the
 * left edge, entering horizontally so the one arrowhead orientation still fits. Square
 * corners with a small radius, so it reads as the same kind of line as every other edge
 * rather than as an ellipse drawn around the node.
 */
function selfCallGeometry(
  sourceX: number,
  sourceY: number,
  targetX: number,
  targetY: number,
): EdgeGeometry {
  const apexY = Math.min(sourceY, targetY) - LOOP_RISE;
  const tip = targetX - ARROW_LENGTH;
  const right = sourceX + LOOP_REACH;
  const left = tip - LOOP_REACH;
  const bend = EDGE_CORNER_RADIUS * 2;

  return {
    path:
      `M ${sourceX} ${sourceY} H ${right - bend} ` +
      `Q ${right} ${sourceY} ${right} ${sourceY - bend} ` +
      `V ${apexY + bend} Q ${right} ${apexY} ${right - bend} ${apexY} ` +
      `H ${left + bend} Q ${left} ${apexY} ${left} ${apexY + bend} ` +
      `V ${targetY - bend} Q ${left} ${targetY} ${left + bend} ${targetY} ` +
      `H ${tip}`,
    labelX: (sourceX + targetX) / 2,
    labelY: apexY,
  };
}
