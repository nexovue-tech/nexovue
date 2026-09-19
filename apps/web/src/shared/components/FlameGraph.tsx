/**
 * A flame graph, drawn as an icicle: the root on top, callees below, each box as
 * wide as its share of the whole.
 *
 * It answers one question — where did the total go — so width is the only thing a
 * box's size means, and the key above the graph says so (F17, FR12): the most common
 * misreading of a flame graph is to take left-to-right as time. Colour marks the
 * module a frame belongs to so one library reads as one band; it never encodes a
 * value, so no colour can be mistaken for "bad". A comparison is the one exception,
 * and says so in its legend: there colour is the change, and nothing else.
 *
 * Plain positioned buttons rather than a canvas: the API prunes a graph to a couple
 * of thousand frames (NFR4), which the DOM draws without effort, and every box stays
 * reachable by keyboard, readable by assistive technology, and testable.
 */

import { useMemo, useState } from "react";

/** One box as the API sends it. */
export interface FlameNode {
  name: string;
  file: string;
  unsymbolized: boolean;
  total: number;
  self_value: number;
  /** Greater than zero: this box stands for that many frames folded for size. */
  merged: number;
  /** The source line, when every frame merged into this box was on the same one. */
  line?: number | null;
  children: FlameNode[];
  /** In a comparison, this frame's share of each side's whole, from 0 to 1. */
  baseline_share?: number;
  comparison_share?: number;
}

/** A function the reader picked elsewhere — the functions table — to find here. */
export interface FrameIdentity {
  name: string;
  file: string;
}

interface FlameGraphProps {
  root: FlameNode;
  /** A value in the profile's own unit, for the tooltip. */
  formatValue: (value: number) => string;
  /** How many frames the API folded to keep the graph readable. */
  mergedFrames: number;
  /** Highlights every frame whose function contains this, ignoring case (FR8). */
  search?: string;
  /** Highlights every frame of this one function. */
  selected?: FrameIdentity | null;
  /** A comparison: colour is the change in share, and the tooltip gives both sides. */
  difference?: boolean;
}

/** Boxes narrower than this share of the view are not drawn: a sliver of a pixel. */
const MIN_SHARE = 0.001;
/** A box shows its name only when it is wide enough to be read. */
const LABEL_SHARE = 0.03;
const ROW_HEIGHT_PX = 22;
/** A change in share smaller than this — a tenth of a percentage point — is no change. */
const SIGNIFICANT_CHANGE = 0.001;

/** Literal classes, so the stylesheet keeps them: one per categorical series. */
const MODULE_EDGES = [
  "border-l-series-1",
  "border-l-series-2",
  "border-l-series-3",
  "border-l-series-4",
  "border-l-series-5",
  "border-l-series-6",
];

type Change = "more" | "less" | "same" | "new" | "gone";

const CHANGE_STYLES: Record<Change, string> = {
  more: "border-critical-border bg-critical-subtle text-critical-text",
  new: "border-critical-border border-dashed bg-critical-subtle text-critical-text",
  less: "border-info-border bg-info-subtle text-info-text",
  gone: "border-info-border border-dashed bg-info-subtle text-info-text",
  same: "border-line-subtle bg-raised text-ink",
};

const CHANGE_GLYPHS: Record<Change, string> = {
  more: "▲ ",
  new: "▲ ",
  less: "▼ ",
  gone: "▼ ",
  same: "",
};

const CHANGE_WORDS: Record<Change, string> = {
  more: "a larger share",
  new: "only in the comparison",
  less: "a smaller share",
  gone: "only in the baseline",
  same: "about the same share",
};

interface Placed {
  node: FlameNode;
  /** The frames from the root's child down to this one, as identities: where it is,
      in terms that survive the graph being fetched again. */
  trail: string[];
  /** A key unique within this render. */
  path: string;
  depth: number;
  /** Fractions of the whole width. */
  left: number;
  width: number;
}

export function FlameGraph({
  root,
  formatValue,
  mergedFrames,
  search = "",
  selected = null,
  difference = false,
}: FlameGraphProps) {
  const [focusTrail, setFocusTrail] = useState<string[]>([]);
  const trail = useMemo(() => trailTo(root, focusTrail), [root, focusTrail]);
  const focus = trail[trail.length - 1];
  const placed = place(trail);
  const depth = placed.reduce((deepest, box) => Math.max(deepest, box.depth), 0) + 1;

  const looking = useMemo(() => lookingFor(search, selected), [search, selected]);
  const matched = looking ? matchedShare(root, looking.matches) : null;

  return (
    <figure className="flex flex-col gap-2" aria-label="Flame graph">
      <figcaption className="text-ink-dim text-xs">
        {difference
          ? "Width is each function's share of the two profiles together. Colour is how its share changed from the baseline — red grew, blue shrank."
          : "Width is each function's share of the total — not time. Top is the entry point; the widest box at the bottom of a stack is where most of it went."}
        {mergedFrames > 0 ? ` ${mergedFrames.toLocaleString("en")} small frames are merged.` : ""}
      </figcaption>
      {difference ? <Legend /> : null}
      {looking && matched ? (
        <p role="status" className="text-ink text-xs">
          {matched.frames === 0
            ? `No frame matches “${looking.term}”.`
            : `${matched.frames.toLocaleString("en")} ${matched.frames === 1 ? "frame matches" : "frames match"} “${looking.term}” — ${formatPercent(matched.share)} of the total.`}
        </p>
      ) : null}
      {trail.length > 1 ? (
        <nav aria-label="Zoom" className="flex flex-wrap items-center gap-1 text-xs">
          {trail.map((node, index) => {
            const current = index === trail.length - 1;
            return (
              <span key={focusTrail.slice(0, index).join("/")} className="flex items-center gap-1">
                {index > 0 ? <span className="text-ink-dim">›</span> : null}
                <button
                  type="button"
                  disabled={current}
                  aria-current={current ? "location" : undefined}
                  onClick={() => setFocusTrail(trail.slice(1, index + 1).map(identityOf))}
                  className="max-w-48 truncate text-brand-text hover:underline disabled:text-ink disabled:no-underline"
                >
                  {index === 0 ? "All" : frameLabel(node)}
                </button>
              </span>
            );
          })}
        </nav>
      ) : null}
      <div className="relative w-full overflow-hidden" style={{ height: depth * ROW_HEIGHT_PX }}>
        {placed.map((box) => (
          <Box
            key={box.path}
            box={box}
            root={root}
            formatValue={formatValue}
            difference={difference}
            highlighted={looking ? looking.matches(box.node) : null}
            isFocus={box.node === focus && trail.length > 1}
            onZoom={() => setFocusTrail(box.trail)}
          />
        ))}
      </div>
    </figure>
  );
}

function Box({
  box,
  root,
  formatValue,
  difference,
  highlighted,
  isFocus,
  onZoom,
}: {
  box: Placed;
  root: FlameNode;
  formatValue: (value: number) => string;
  difference: boolean;
  /** Null when nothing is being looked for. */
  highlighted: boolean | null;
  isFocus: boolean;
  onZoom: () => void;
}) {
  const { node } = box;
  const share = root.total > 0 ? node.total / root.total : 0;
  const change = difference ? changeOf(node) : null;
  // The change is carried by a glyph as well as a colour, so it reads without either
  // hue (principles rule 6).
  const label = change ? `${CHANGE_GLYPHS[change]}${frameLabel(node)}` : frameLabel(node);
  const title = [
    label,
    node.file ? (node.line ? `${node.file}:${node.line}` : node.file) : null,
    node.unsymbolized
      ? "Unsymbolized: the profiler had no symbols for this native code, so it is shown by its binary."
      : null,
    change
      ? `Baseline ${formatPercent(node.baseline_share ?? 0)} → comparison ${formatPercent(node.comparison_share ?? 0)}: ${CHANGE_WORDS[change]}`
      : `Total ${formatValue(node.total)} (${formatPercent(share)})`,
    !change && node.self_value > 0 ? `Self ${formatValue(node.self_value)}` : null,
    "Click to zoom in.",
  ]
    .filter(Boolean)
    .join("\n");

  // Spoken in words, not glyphs: a screen reader reads "▲" as a shape, not a change.
  const spoken = change
    ? `${frameLabel(node)}, ${CHANGE_WORDS[change]}: ${formatPercent(node.baseline_share ?? 0)} before, ${formatPercent(node.comparison_share ?? 0)} after`
    : `${label}, ${formatPercent(share)} of the total`;

  return (
    <button
      type="button"
      aria-label={spoken}
      title={title}
      onClick={onZoom}
      data-unsymbolized={node.unsymbolized || undefined}
      data-merged={node.merged > 0 || undefined}
      data-matched={highlighted === true || undefined}
      data-change={change ?? undefined}
      className={`absolute truncate rounded-sm border px-1 text-left text-xs leading-5 focus-visible:outline-2 focus-visible:outline-focus ${
        change ? CHANGE_STYLES[change] : frameStyle(node)
      } ${highlighted === true ? "outline-2 outline-focus" : ""} ${
        highlighted === false ? "opacity-40" : ""
      } ${isFocus ? "font-medium" : ""}`}
      style={{
        top: box.depth * ROW_HEIGHT_PX,
        left: `${box.left * 100}%`,
        width: `calc(${box.width * 100}% - 1px)`,
        height: ROW_HEIGHT_PX - 2,
      }}
    >
      {box.width >= LABEL_SHARE ? label : null}
    </button>
  );
}

function Legend() {
  const shown: Change[] = ["more", "new", "less", "gone", "same"];
  return (
    <ul aria-label="What the colours mean" className="flex flex-wrap gap-3 text-ink-dim text-xs">
      {shown.map((change) => (
        <li key={change} className="flex items-center gap-1">
          <span
            aria-hidden="true"
            className={`inline-block h-3 w-4 rounded-sm border text-center text-2xs leading-3 ${CHANGE_STYLES[change]}`}
          >
            {CHANGE_GLYPHS[change].trim()}
          </span>
          {CHANGE_WORDS[change]}
        </li>
      ))}
    </ul>
  );
}

/** What a frame is called on screen. */
export function frameLabel(node: FlameNode): string {
  if (node.merged > 0) return `${node.merged.toLocaleString("en")} frames merged`;
  if (node.unsymbolized) return `${node.name} (unsymbolized)`;
  return node.name;
}

function frameStyle(node: FlameNode): string {
  if (node.merged > 0) return "border-line bg-active text-ink-dim";
  if (node.unsymbolized) return "border-dashed border-line bg-surface text-ink-dim italic";
  return `border-line-subtle border-l-4 bg-raised text-ink ${MODULE_EDGES[moduleIndex(node.name)]}`;
}

/** How a frame's share moved between the two sides of a comparison. */
function changeOf(node: FlameNode): Change {
  const before = node.baseline_share ?? 0;
  const after = node.comparison_share ?? 0;
  if (before === 0 && after > 0) return "new";
  if (after === 0 && before > 0) return "gone";
  if (after - before >= SIGNIFICANT_CHANGE) return "more";
  if (before - after >= SIGNIFICANT_CHANGE) return "less";
  return "same";
}

/** The module a frame belongs to: its name up to the first separator a language uses. */
function moduleIndex(name: string): number {
  const module = name.split(/::|[./(]/, 1)[0] ?? name;
  let hash = 0;
  for (const character of module) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return hash % MODULE_EDGES.length;
}

/** A frame as the reader knows it — what it is, not where it sits in this render. */
function identityOf(node: FlameNode): string {
  return [node.name, node.file, node.unsymbolized, node.merged > 0].join("\u0000");
}

/**
 * The nodes from the root down to the focused frame. The focus is kept as
 * identities, so a zoom survives the graph being fetched again; where a frame is
 * gone from the new graph, the trail stops at the deepest one still there.
 */
function trailTo(root: FlameNode, identities: string[]): FlameNode[] {
  const trail = [root];
  let node = root;
  for (const identity of identities) {
    const next = node.children.find((child) => identityOf(child) === identity);
    if (next === undefined) break;
    trail.push(next);
    node = next;
  }
  return trail;
}

/**
 * Every drawable box. The trail to the focused frame is drawn full width above it,
 * as context; the focused frame's subtree fills the width below, as fractions of it.
 */
function place(trail: FlameNode[]): Placed[] {
  const identities = trail.slice(1).map(identityOf);
  const focus = trail[trail.length - 1];
  const placed: Placed[] = trail.slice(0, -1).map((node, depth) => ({
    node,
    trail: identities.slice(0, depth),
    path: `^${depth}`,
    depth,
    left: 0,
    width: 1,
  }));
  const whole = focus.total;
  if (whole <= 0) return placed;

  const visit = (
    node: FlameNode,
    nodeTrail: string[],
    path: string,
    depth: number,
    left: number,
  ) => {
    const width = node.total / whole;
    if (width < MIN_SHARE) return;
    placed.push({ node, trail: nodeTrail, path, depth, left, width });
    let offset = left;
    node.children.forEach((child, index) => {
      visit(child, [...nodeTrail, identityOf(child)], `${path}.${index}`, depth + 1, offset);
      offset += child.total / whole;
    });
  };
  visit(focus, identities, "", trail.length - 1, 0);
  return placed;
}

type Matcher = (node: FlameNode) => boolean;

/** What the reader is looking for, and the words to echo it back in; null for nothing. */
function lookingFor(
  search: string,
  selected: FrameIdentity | null,
): { matches: Matcher; term: string } | null {
  if (selected) {
    return {
      term: selected.name,
      matches: (node) =>
        node.merged === 0 && node.name === selected.name && node.file === selected.file,
    };
  }
  const term = search.trim();
  if (term === "") return null;
  const lowered = term.toLowerCase();
  return {
    term,
    matches: (node) => node.merged === 0 && node.name.toLowerCase().includes(lowered),
  };
}

/**
 * How many frames match, and their combined share of the whole. A match inside
 * another match is counted as a frame but not again as share — a recursive
 * function's time is spent once, however deep it calls itself.
 */
function matchedShare(root: FlameNode, matches: Matcher): { frames: number; share: number } {
  let frames = 0;
  let value = 0;
  const visit = (node: FlameNode, inside: boolean) => {
    const hit = node !== root && matches(node);
    if (hit) {
      frames += 1;
      if (!inside) value += node.total;
    }
    for (const child of node.children) visit(child, inside || hit);
  };
  visit(root, false);
  return { frames, share: root.total > 0 ? value / root.total : 0 };
}

function formatPercent(share: number): string {
  const percent = share * 100;
  return percent < 10 ? `${percent.toFixed(1)} %` : `${Math.round(percent)} %`;
}
