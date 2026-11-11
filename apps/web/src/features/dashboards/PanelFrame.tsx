import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import { adoptScreenOverride } from "@/shared/selection";

import { PANEL_ID_ATTRIBUTE } from "./visible-panels";

/**
 * One panel's frame: a `<section>` named by its `<h2>`, so a screen reader can move
 * between thirty of them (NFR5, AC111), and the states that have no line to draw.
 */

export function PanelFrame({
  panelId,
  service,
  title,
  span,
  watch,
  children,
}: {
  panelId: string;
  service?: string;
  title: string;
  span: string;
  /** Told when the frame mounts, to follow whether it is on screen (FR53). */
  watch?: (element: HTMLElement | null) => void;
  children: ReactNode;
}) {
  const headingId = `panel-${panelId}-title`;
  return (
    <section
      ref={watch}
      {...{ [PANEL_ID_ATTRIBUTE]: panelId }}
      id={`panel-${panelId}`}
      aria-labelledby={headingId}
      tabIndex={-1}
      className={`col-span-12 rounded-sm border border-line p-3 ${span}`}
    >
      <h2 id={headingId} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {service === undefined ? (
          title
        ) : (
          <>
            {/* The service is the click target: a line on a canvas cannot be (FR35). */}
            {/* Followed on purpose, so the project and window shown here go with it (FR35). */}
            <Link
              to="/services/$name"
              params={{ name: service }}
              onClick={adoptScreenOverride}
              className="hover:text-ink"
            >
              {service}
            </Link>
            {` · ${title}`}
          </>
        )}
      </h2>
      {children}
    </section>
  );
}

/** Words in place of a line, held at the chart's height so a row stays level. */
export function PanelWords({
  words,
  correlationId,
  onReadAgain,
}: {
  words: string;
  correlationId?: string | null;
  onReadAgain?: () => void;
}) {
  return (
    <div
      className="flex flex-col items-start justify-center gap-2 text-ink-dim text-sm"
      style={{ minHeight: CHART_HEIGHT }}
    >
      <p>{words}</p>
      {onReadAgain ? (
        <button type="button" onClick={onReadAgain} className="text-brand text-sm underline">
          Read again
        </button>
      ) : null}
      {correlationId ? <CorrelationId value={correlationId} /> : null}
    </div>
  );
}

/** Selectable, so it can be quoted to support (FR60). */
export function CorrelationId({ value }: { value: string }) {
  return (
    <p className="font-mono text-2xs text-ink-dim">
      Reference <span className="select-all">{value}</span>
    </p>
  );
}

/**
 * The filled strip over a line kept from an earlier read (FR31): dimming alone is not used.
 * Not a live region: the page has one, and a strip per panel would talk over it (AC143).
 */
export function StaleStrip({ since }: { since: string }) {
  return (
    <p className="mb-2 rounded-xs border border-warning-border bg-warning-subtle px-2 py-1 text-sm text-warning-text">
      Could not read since {since}. Line below is old.
    </p>
  );
}
