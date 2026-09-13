import { useNavigate, useSearch } from "@tanstack/react-router";
import { ReactFlow } from "@xyflow/react";
import { useMemo } from "react";

import { Button } from "@/shared/components/Button";
import { EmptyState } from "@/shared/components/EmptyState";
import { appliedFilters, FilterBar } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { filterParams, parseFilterValues, withFilter } from "@/shared/filters";
import { plural } from "@/shared/format/plural";
import { timeRangeById, useLiveWindow, useSelection } from "@/shared/selection";

import "@xyflow/react/dist/base.css";
import "./service-map.css";

import { useServiceMap } from "./api";
import { DependencyEdge } from "./DependencyEdge";
import { SERVICE_MAP_FILTERS } from "./filters";
import { NODE_HEIGHT, NODE_WIDTH } from "./graph-layout";
import { ServiceNode } from "./ServiceNode";
import { buildServiceGraph, DEPENDENCY_EDGE_TYPE, SERVICE_NODE_TYPE } from "./service-graph";

/**
 * Which services call which, over the selected window.
 *
 * Every arrow here was derived from spans you already send: a client span in one
 * service and a server span in another are the same call seen twice, and the pair is
 * the edge. Nothing was configured to produce this picture.
 *
 * The screen answers one question first — *is any dependency failing* — so the failing
 * edges carry their counts on the map itself rather than behind a hover.
 */

// Defined once, outside the component: React Flow treats a new object identity as a
// new set of types and remounts every node and edge on it.
const NODE_TYPES = { [SERVICE_NODE_TYPE]: ServiceNode };
const EDGE_TYPES = { [DEPENDENCY_EDGE_TYPE]: DependencyEdge };

// maxZoom 1 so a two-service map is not blown up to fill the pane: the node is sized
// for reading, and a graph that fits should be drawn at the size it was designed at.
const FIT_VIEW = { padding: 0.1, maxZoom: 1 };
const ZOOM_RANGE = { min: 0.25, max: 2 };

export function ServiceMapScreen() {
  const { projectId, rangeId } = useSelection();
  const timeWindow = useLiveWindow();
  const navigate = useNavigate();

  // The route's own id, not its path: `/service-map` renders inside the shell.
  const search = useSearch({ from: "/shell/service-map" });
  const filters = useMemo(() => parseFilterValues(SERVICE_MAP_FILTERS, search), [search]);
  const narrowed = appliedFilters(SERVICE_MAP_FILTERS, filters).length > 0;

  // `replace`, not push: the back button belongs to the screen a person came
  // from, not to every control they tried on the way.
  const show = (next: Record<string, string | undefined>) =>
    void navigate({ to: "/service-map", search: () => filterParams(next), replace: true });

  const map = useServiceMap(projectId, timeWindow, filterParams(filters));

  const graph = useMemo(() => buildServiceGraph(map.data), [map.data]);

  return (
    <div className="service-map flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Service map</h1>
        <p className="mt-1 text-ink-dim text-sm">
          {map.data && graph.nodes.length > 0 ? (
            <MapSummary
              serviceCount={graph.nodes.length}
              edgeCount={graph.edges.length}
              failingEdgeCount={graph.failingEdgeCount}
              rangeLabel={timeRangeById(rangeId).label}
            />
          ) : (
            <>Who calls whom in the last {timeRangeById(rangeId).label}, taken from your spans.</>
          )}
        </p>
      </header>

      <FilterBar
        definitions={SERVICE_MAP_FILTERS}
        values={filters}
        onChange={(key, value) => show(withFilter(filters, key, value))}
        onClearAll={() => show({})}
      />

      {map.isPending ? (
        <MapSkeleton />
      ) : map.isError ? (
        <div className="px-4">
          <InlineFailure
            title="The service map could not be loaded"
            error={map.error}
            onRetry={() => map.refetch()}
          />
        </div>
      ) : graph.nodes.length === 0 ? (
        // A map with no failing edges is the result you want; a map with no
        // edges at all is usually a missing exporter. Saying "no calls" to both
        // reports a healthy system as a broken one.
        narrowed ? (
          <EmptyState
            title="No dependencies match these filters"
            detail="Services called each other in this window, but no edge matches what you asked for. If you asked for the failing ones, none of them failed."
            action={
              <Button variant="secondary" onClick={() => show({})}>
                Show the whole map
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No cross-service calls in this window"
            detail={`Nothing called anything else in the last ${timeRangeById(rangeId).label}. An application that is a single service will legitimately show nothing here — the map is drawn from calls between services, so one service has no map. Widen the time range, or check that both sides of a call are exporting.`}
          />
        )
      ) : (
        <>
          <MapLegend />
          <div className="min-h-0 flex-1">
            <ReactFlow
              nodes={graph.nodes}
              edges={graph.edges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              fitView
              fitViewOptions={FIT_VIEW}
              minZoom={ZOOM_RANGE.min}
              maxZoom={ZOOM_RANGE.max}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              panOnScroll
              proOptions={{ hideAttribution: false }}
            />
          </div>
        </>
      )}
    </div>
  );
}

/** The sentence the screen exists to say, before any of the evidence. */
function MapSummary({
  serviceCount,
  edgeCount,
  failingEdgeCount,
  rangeLabel,
}: {
  serviceCount: number;
  edgeCount: number;
  failingEdgeCount: number;
  rangeLabel: string;
}) {
  return (
    <>
      {serviceCount} {plural(serviceCount, "service")} and {edgeCount}{" "}
      {plural(edgeCount, "dependency", "dependencies")} in the last {rangeLabel}
      {failingEdgeCount > 0 ? (
        <>
          {" — "}
          <span className="font-medium text-critical-text">
            {failingEdgeCount} {plural(failingEdgeCount, "dependency", "dependencies")} dropping
            calls
          </span>
          .
        </>
      ) : (
        <>, none of them dropping calls.</>
      )}
    </>
  );
}

/** What the drawing means, said in words. Colour is never the only carrier. */
function MapLegend() {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-line-subtle border-y bg-surface px-4 py-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps">
      <span>Left to right, in the direction calls travel</span>
      <span>Thicker line, more calls</span>
      <span>Dashed, queued</span>
      <span className="text-critical-text">Coloured, dropping calls</span>
    </div>
  );
}

/**
 * The shape of the map, not a spinner. Columns of node-sized blocks in the same
 * left-to-right arrangement the graph arrives in, so the layout holds still.
 *
 * The blocks are described up front rather than keyed by loop index: a placeholder
 * node has no identity of its own, so the description carries one.
 */
const SKELETON_COLUMNS = [2, 3, 2].map((height, column) => ({
  id: `map-skeleton-column-${column}`,
  nodes: Array.from({ length: height }, (_, row) => `map-skeleton-node-${column}-${row}`),
}));

function MapSkeleton() {
  return (
    <div
      className="flex min-h-0 flex-1 items-center justify-center gap-24 px-4"
      role="status"
      aria-live="polite"
      aria-label="Loading the service map"
    >
      {SKELETON_COLUMNS.map((column) => (
        <div key={column.id} className="flex flex-col gap-8">
          {column.nodes.map((nodeId) => (
            <span
              key={nodeId}
              className="block animate-pulse rounded-sm bg-raised"
              style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
