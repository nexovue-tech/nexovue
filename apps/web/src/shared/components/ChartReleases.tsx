import { useCallback, useState } from "react";

import type { DeployMarker, MarkerCluster } from "../deploy-markers";
import { markersLine, truncatedLine } from "../deploy-markers";
import { Button } from "./Button";
import type { BoxAnchor } from "./ReleaseHoverBox";
import { ReleaseFacts, ReleaseHoverBox } from "./ReleaseHoverBox";

/** The strip's height above a plot: one line of small tags. */
export const STRIP_HEIGHT = 18;
/** A plain deploy's tag where it has no word: a short mark, still a button. */
const PLAIN_MARK = "▾";
/** The box opens this far under its tag. */
const BOX_GAP = 4;

interface Opened {
  index: number;
  pinned: boolean;
  anchor: BoxAnchor;
}

function tagLabel(cluster: MarkerCluster): string {
  const [only] = cluster.markers;
  if (cluster.markers.length > 1 || only === undefined) return `${cluster.markers.length} releases`;
  return `${only.version} of ${only.service}${cluster.tag ? `, ${cluster.tag}` : ""}`;
}

/**
 * The words above a plot (design, *Release markers on charts*): an HTML tag over each
 * mark, placed by the chart's own `valToPos`, since a letter painted on the canvas would
 * sit under the series and be overprinted. Hover or focus shows the facts; a click pins
 * them with a link, so a box that would otherwise follow the cursor can be reached.
 */
export function ReleaseStrip({
  clusters,
  plotLeft,
  zone,
}: {
  clusters: readonly MarkerCluster[];
  plotLeft: number;
  zone?: string;
}) {
  const [opened, setOpened] = useState<Opened | null>(null);
  const close = useCallback(() => setOpened(null), []);
  const show = (index: number, element: HTMLElement, pinned: boolean) => {
    const box = element.getBoundingClientRect();
    setOpened({ index, pinned, anchor: { left: box.left, top: box.bottom + BOX_GAP } });
  };
  const shown = opened === null ? undefined : clusters[opened.index];

  return (
    <div className="relative w-full" style={{ height: STRIP_HEIGHT }}>
      {clusters.map((cluster, index) => (
        <button
          key={cluster.markers.map((marker) => marker.key).join(",")}
          type="button"
          aria-label={tagLabel(cluster)}
          aria-expanded={opened?.index === index && opened.pinned}
          className="absolute top-0 -translate-x-1/2 whitespace-nowrap px-0.5 font-mono text-2xs text-ink-dim leading-none hover:text-ink"
          style={{ left: plotLeft + cluster.left }}
          onMouseEnter={(event) => {
            if (!opened?.pinned) show(index, event.currentTarget, false);
          }}
          onMouseLeave={() => {
            if (!opened?.pinned) close();
          }}
          onFocus={(event) => {
            if (!opened?.pinned) show(index, event.currentTarget, false);
          }}
          onBlur={() => {
            if (!opened?.pinned) close();
          }}
          onClick={(event) => show(index, event.currentTarget, true)}
        >
          {cluster.tag ?? PLAIN_MARK}
        </button>
      ))}
      {shown !== undefined && opened !== null ? (
        <ReleaseHoverBox
          markers={shown.markers}
          zone={zone}
          pinned={opened.pinned}
          anchor={opened.anchor}
          onClose={close}
        />
      ) : null}
    </div>
  );
}

export interface ReleaseRead {
  markers: readonly DeployMarker[];
  isError: boolean;
  onRetry: () => void;
  truncated: boolean;
  cap: number;
}

/**
 * Once under each chart group (FR36): the always-visible line saying what the dashes
 * mean and how many, the cap sentence (FR35), and every mark as text, which is how
 * touch, keyboard and screen-reader users get them (AC39). Open by default on a
 * finding's page, closed elsewhere. A failed read says so: never an unmarked chart.
 */
export function ChartReleases({
  read,
  zone,
  open = false,
}: {
  read: ReleaseRead;
  zone?: string;
  open?: boolean;
}) {
  if (read.isError) {
    return (
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <p className="text-ink-dim">Releases could not be loaded for this chart.</p>
        <Button variant="secondary" onClick={read.onRetry}>
          Try again
        </Button>
      </div>
    );
  }
  const count = read.markers.length;
  return (
    <div className="flex flex-col gap-1 text-ink-dim text-xs">
      <p className="max-w-prose">{markersLine(count)}</p>
      {read.truncated ? <p>{truncatedLine(read.cap)}</p> : null}
      {count > 0 ? (
        <details open={open}>
          <summary className="cursor-pointer text-ink">Releases in this window ({count})</summary>
          <div className="pt-2">
            <ReleaseFacts markers={[...read.markers].reverse()} zone={zone} pinned />
          </div>
        </details>
      ) : null}
    </div>
  );
}
