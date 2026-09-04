import { Link } from "@tanstack/react-router";
import { Handle, type NodeProps, Position } from "@xyflow/react";

import { formatCompactRate } from "@/shared/format/rate";

import type { Health, ServiceNode as ServiceNodeShape } from "./service-graph";

/**
 * One service on the map: a name and a number.
 *
 * Not a card. The box is a one-pixel rule and a surface, the type carries the
 * hierarchy, and the only saturated colour on it is the left bar that appears when
 * calls into this service are failing.
 */

/** Identity, not health. Written out because Tailwind reads class names literally. */
const SERIES_DOT = [
  "bg-series-1",
  "bg-series-2",
  "bg-series-3",
  "bg-series-4",
  "bg-series-5",
  "bg-series-6",
];

/** Health is a left accent bar, the same signal the trace list uses for a failed trace. */
const HEALTH_BAR: Record<Health, string> = {
  critical: "border-l-critical-solid",
  warning: "border-l-warning-solid",
  healthy: "border-l-transparent",
};

const HEALTH_LABEL: Record<Health, string> = {
  critical: "text-critical-text",
  warning: "text-warning-text",
  healthy: "text-ink-dim",
};

export function ServiceNode({ data }: NodeProps<ServiceNodeShape>) {
  const body = <ServiceNodeBody data={data} />;

  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {data.instrumented ? (
        <Link
          to="/services/$name"
          params={{ name: data.name }}
          // No title here: the body carries one, and a nested title never surfaces.
          className="block h-full w-full focus-visible:outline-2"
        >
          {body}
        </Link>
      ) : (
        body
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </>
  );
}

/** The full sentence the node abbreviates, for the hover the abbreviation earns. */
function describe(data: ServiceNodeShape["data"]): string {
  const addresses = data.addresses ?? [];
  if (addresses.length > 0) {
    const over = data.peerKind ? ` over ${data.peerKind}` : "";
    // The addresses themselves: the node aggregates, and the hover gives the detail
    // back, because the stored rows kept every one of them.
    return `${addresses.length} ${addresses.length === 1 ? "host" : "hosts"} reached${over} by address, with no telemetry of their own — ${addresses.join(", ")}. Addresses change on restart, so they are drawn as one dependency rather than one node each.`;
  }

  if (!data.instrumented) {
    const named = data.peerKind ? ` A caller reached it over ${data.peerKind}.` : "";
    return `${data.name} — something calls this, and it sends us no telemetry of its own.${named}`;
  }

  const traffic =
    data.flow === "serves"
      ? `serves ${formatCompactRate(data.callsPerMinute)} calls a minute`
      : `sends ${formatCompactRate(data.callsPerMinute)} calls a minute`;
  const failures = data.errorCount > 0 ? `, ${data.errorCount} of them failing` : "";
  return `${data.name} ${traffic}${failures}.`;
}

/**
 * The second line of a peer node: how many machines it stands for, or that we have
 * nothing from it. `no telemetry` stays for a single named peer because a name in
 * parentheses is our word for it, not its own.
 */
function describeInstances(addresses: readonly string[] | undefined): string {
  const count = addresses?.length ?? 0;
  if (count === 0) return "no telemetry";
  return `${count} ${count === 1 ? "instance" : "instances"}`;
}

function ServiceNodeBody({ data }: { data: ServiceNodeShape["data"] }) {
  const dot = data.seriesIndex === undefined ? undefined : SERIES_DOT[data.seriesIndex];

  return (
    <div
      className={`flex h-full w-full flex-col justify-center overflow-hidden rounded-sm border border-l-2 bg-surface px-2.5 py-1.5 ${
        data.instrumented ? "border-line-subtle" : "border-line-subtle border-dashed"
      } ${HEALTH_BAR[data.health]} ${data.instrumented ? "hover:bg-hover" : ""}`}
      title={describe(data)}
    >
      <span className="flex items-center gap-1.5">
        {dot ? <span className={`size-1.5 shrink-0 rounded-full ${dot}`} /> : null}
        <span
          className={`truncate font-medium text-sm ${data.instrumented ? "text-ink" : "text-ink-dim italic"}`}
        >
          {data.name}
        </span>
      </span>

      {/* `in`/`out` rather than `served`/`sent`: the failure count has to fit beside it,
          and it is the count that decides whether anyone reads the rest of this node. */}
      <span className="mt-0.5 flex items-baseline gap-1 truncate font-mono text-2xs text-ink-dim">
        {data.instrumented ? (
          <span className="shrink-0">
            {data.flow === "serves" ? "in" : "out"} {formatCompactRate(data.callsPerMinute)}/min
          </span>
        ) : (
          // The technology is the new information, so it leads. For a group of
          // addresses the instance count replaces `no telemetry`, because how many
          // there are is the fact the collapsed node would otherwise have hidden.
          <span className="truncate">
            {data.peerKind ? `${data.peerKind} · ` : ""}
            {describeInstances(data.addresses)}
          </span>
        )}
        {data.errorCount > 0 ? (
          <span className={`shrink-0 ${HEALTH_LABEL[data.health]}`}>
            · {data.errorCount} failed
          </span>
        ) : null}
      </span>
    </div>
  );
}
