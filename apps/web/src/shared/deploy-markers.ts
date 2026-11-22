/**
 * The release marks drawn across a trend chart (F28, design *Release markers on charts*).
 *
 * This is IA3: the question a chart with a step in it always raises is "did a deploy
 * do this", and answering it should not mean opening another screen and comparing two
 * clocks by eye. A mark is order, never cause: it says what came when.
 *
 * Read for the window on screen from `GET /releases/markers`: each deploy a sender
 * reported, and each version first seen in traffic. On the canvas every mark is the same
 * neutral dash; the strip above the plot carries the words, so nothing rests on colour
 * or dash alone (FR37).
 */

export type DeployState = "started" | "finished" | "failed" | "rolled_back";

/** One mark as the API serves it. No sender text (AC45). */
export interface ReleaseMark {
  release_id: string | null;
  period_id: string | null;
  deploy_id: string | null;
  service_name: string;
  version: string;
  at: string;
  basis: "reported" | "seen";
  state: DeployState | null;
}

/** One mark on the chart, at a moment on its x axis. */
export interface DeployMarker {
  /** Unix **seconds**, which is what uPlot's time scale is in — not millis. */
  at: number;
  version: string;
  service: string;
  /** Told by CI, or seen in traffic. */
  source: "reported" | "seen";
  state: DeployState | null;
  /** The release a click opens; null once the release itself has gone. */
  releaseId: string | null;
  key: string;
}

/** A tag naming one or more marks that sit too close to tell apart. */
export interface MarkerCluster {
  /** Where the tag sits, in the plot's own CSS pixels. */
  left: number;
  markers: DeployMarker[];
  /** "failed", "rolled back", "seen", "3 releases", or none for a plain deploy. */
  tag: string | null;
}

/** Marks closer than this collapse into one tag with the count (a guess). */
export const COLLAPSE_WITHIN_PX = 8;

const MILLISECONDS_PER_SECOND = 1000;

/**
 * The marks of one service that fall inside the window being charted, oldest first.
 *
 * A mark outside it draws nothing rather than being clamped to an edge: a rule pinned to
 * the left of the chart would say "this happened at the start of the window", which is a
 * claim about a time nobody measured. With no service named — Overview — every mark in
 * the window is drawn, each naming its service (FR36).
 */
export function releaseMarkers(
  marks: readonly ReleaseMark[],
  options: { serviceName?: string; from: string; to: string },
): DeployMarker[] {
  const from = Date.parse(options.from);
  const to = Date.parse(options.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return [];

  const markers: DeployMarker[] = [];
  for (const mark of marks) {
    if (options.serviceName !== undefined && mark.service_name !== options.serviceName) continue;
    const at = Date.parse(mark.at);
    if (!Number.isFinite(at) || at < from || at > to) continue;
    markers.push({
      at: at / MILLISECONDS_PER_SECOND,
      version: mark.version,
      service: mark.service_name,
      source: mark.basis,
      state: mark.state,
      releaseId: mark.release_id,
      key: mark.deploy_id ?? mark.period_id ?? `${mark.service_name}:${mark.version}:${mark.at}`,
    });
  }
  return markers.sort((left, right) => left.at - right.at);
}

/** The word a mark carries, when it is one a reader must not miss (FR37). */
export function markerTag(marker: DeployMarker): string | null {
  if (marker.state === "failed") return "failed";
  if (marker.state === "rolled_back") return "rolled back";
  if (marker.source === "seen") return "seen";
  return null;
}

/** What a mark with no word of its own shows: a short mark, still a button. */
export const PLAIN_MARK = "▾";

/** One character of a tag's small monospace words, in CSS pixels (a guess, 11 px type). */
export const TAG_CHAR_PX = 7;

function tagOf(markers: readonly DeployMarker[]): string | null {
  const [only] = markers;
  return markers.length > 1 || only === undefined ? `${markers.length} releases` : markerTag(only);
}

function tagWidth(markers: readonly DeployMarker[]): number {
  return (tagOf(markers) ?? PLAIN_MARK).length * TAG_CHAR_PX;
}

/**
 * Marks in pixel order, folded into one tag with the count when their dashes are within
 * `COLLAPSE_WITHIN_PX`, or when their tags would print over each other: a tag is centred
 * on its dash and far wider than the dash gap, so without the second rule two near runs
 * each said "2 releases" in the same place. `position` is the chart's own `valToPos`, so
 * a tag and its dash agree about where a moment is.
 */
export function clusterMarkers(
  markers: readonly DeployMarker[],
  position: (seconds: number) => number,
): MarkerCluster[] {
  const placed = markers
    .map((marker) => ({ marker, left: position(marker.at) }))
    .filter((each) => Number.isFinite(each.left))
    .sort((one, other) => one.left - other.left);
  const clusters: MarkerCluster[] = [];
  for (const { marker, left } of placed) {
    const last = clusters.at(-1);
    const reach = Math.max(
      COLLAPSE_WITHIN_PX,
      (tagWidth(last?.markers ?? []) + tagWidth([marker])) / 2,
    );
    if (last !== undefined && left - last.left <= reach) {
      last.markers.push(marker);
    } else {
      clusters.push({ left, markers: [marker], tag: null });
    }
  }
  for (const cluster of clusters) cluster.tag = tagOf(cluster.markers);
  return clusters;
}

/** The always-visible line under a chart group (FR36). */
export function markersLine(count: number): string {
  if (count === 0) return "No release in this window.";
  return `Dashed lines mark the ${count} ${count === 1 ? "release" : "releases"} in this window. A tag names failed, rolled-back and seen-only ones.`;
}

/** When more marks fell in the window than the read carries (FR35). */
export function truncatedLine(cap: number): string {
  return `Showing the newest ${cap} of this window.`;
}
