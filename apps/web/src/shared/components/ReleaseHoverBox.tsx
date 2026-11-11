import { Link } from "@tanstack/react-router";
import { X } from "lucide-react";
import { useEffect } from "react";
import { createPortal } from "react-dom";

import type { DeployMarker } from "../deploy-markers";
import { formatZonedTime } from "../format/zoned";

/** How the record knows of a mark (BR3): told by CI, or counted in traffic. */
const SOURCE_WORDS: Record<DeployMarker["source"], string> = {
  reported: "Reported by your CI",
  seen: "Seen in traffic",
};

const STATE_WORDS: Partial<Record<NonNullable<DeployMarker["state"]>, string>> = {
  started: "CI reported it started",
  failed: "CI reported it failed",
  rolled_back: "CI reported it rolled back",
};

const BOX =
  "fixed z-50 w-72 max-w-[calc(100vw-2rem)] rounded-sm border border-line bg-raised p-3 shadow-overlay";

/** Where the box opens: under its tag, in the viewport's pixels. */
export interface BoxAnchor {
  left: number;
  top: number;
}

/**
 * The facts of the marks under one tag (design, *Release markers on charts*): service,
 * version, how we know, state and time. Facts only, never a reading. Every name is
 * telemetry text and renders as text, never as markup or a link of its own (AC43). Pinned,
 * each fact links to its release.
 */
export function ReleaseFacts({
  markers,
  zone,
  pinned,
}: {
  markers: readonly DeployMarker[];
  zone?: string;
  pinned: boolean;
}) {
  return (
    <ul className="flex flex-col gap-2">
      {markers.map((marker) => {
        const state = marker.state === null ? undefined : STATE_WORDS[marker.state];
        return (
          <li key={marker.key} className="flex min-w-0 flex-col text-sm">
            <span className="break-words text-ink">
              <span className="font-mono">{marker.version}</span> of {marker.service}
            </span>
            <span className="text-ink-dim">
              {SOURCE_WORDS[marker.source]}
              {state ? `. ${state}` : ""}.{" "}
              {formatZonedTime(new Date(marker.at * 1000).toISOString(), { zone })}
            </span>
            {pinned && marker.releaseId !== null ? (
              <Link
                to="/releases/$releaseId"
                params={{ releaseId: marker.releaseId }}
                className="font-medium text-brand-text underline-offset-2 hover:underline"
              >
                Open this release
              </Link>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The box a hover shows and a click pins, in a portal so the chart's clipping cannot cut
 * it. A pinned box closes on Escape or its close control; a drag on the chart still
 * selects, since the box is never over the plot's own events.
 */
export function ReleaseHoverBox({
  markers,
  zone,
  pinned,
  anchor,
  onClose,
}: {
  markers: readonly DeployMarker[];
  zone?: string;
  pinned: boolean;
  anchor: BoxAnchor;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!pinned) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [pinned, onClose]);

  const placement = { left: anchor.left, top: anchor.top };
  const facts = <ReleaseFacts markers={markers} zone={zone} pinned={pinned} />;
  return createPortal(
    pinned ? (
      <div role="dialog" aria-label="Releases at this moment" className={BOX} style={placement}>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="float-right text-ink-dim hover:text-ink"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
        {facts}
      </div>
    ) : (
      <div role="tooltip" className={BOX} style={placement}>
        {facts}
      </div>
    ),
    document.body,
  );
}
