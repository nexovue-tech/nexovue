import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { formatZonedTime } from "@/shared/format/zoned";
import { adoptScreenOverride } from "@/shared/selection";

import type { ContextFinding, DashboardContext } from "./api";
import type { StripEntry } from "./window-strip";
import { FINDINGS_DIFFER, STRIP_SHOWN, stripEntries, truncationWords } from "./window-strip";

/**
 * "In this window" (F20, FR47, T5.5): the reason for the page is the first thing to read.
 * Open findings first, each as the feed's own sentence with its severity in a word and a
 * real link that keeps the window; closed findings, marked when they ended; alert
 * incidents naming a service (FR67); then releases, a deploy CI reported as "at" and a
 * version only seen in traffic as "first seen in telemetry", never called a deploy (AC122).
 *
 * It is the legend for the shading and rules on the charts, and their keyboard and
 * screen-reader form: shading cannot take focus, and these links can (AC65).
 */
export function WindowStrip({
  context,
  empty,
  now = Date.now(),
}: {
  context: DashboardContext;
  empty: string;
  /** The reader's now, which decides whether a time needs its day said. */
  now?: number;
}) {
  const zone = context.project.timezone;
  const entries = stripEntries(context);
  const [open, setOpen] = useState(false);
  const shown = open ? entries : entries.slice(0, STRIP_SHOWN);
  const hidden = entries.length - shown.length;
  const truncated = truncationWords(context);
  if (entries.length === 0) {
    return (
      <section aria-label="In this window" className="text-ink-dim text-sm">
        <p>In this window: {empty}</p>
      </section>
    );
  }
  return (
    <section aria-label="In this window" className="flex flex-col gap-1 text-sm">
      <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">In this window</p>
      <ul className="flex flex-col gap-1">
        {shown.map((entry) => (
          <li key={entry.key}>
            <EntryLine entry={entry} zone={zone} now={now} />
          </li>
        ))}
      </ul>
      {hidden > 0 || open ? (
        <button
          type="button"
          onClick={() => setOpen((before) => !before)}
          aria-expanded={open}
          className="inline-flex min-h-11 items-center self-start text-brand text-xs underline md:min-h-0"
        >
          {open ? "Show fewer" : `+${hidden} more`}
        </button>
      ) : null}
      {entries.some((entry) => entry.kind === "finding") ? (
        <p className="text-ink-dim text-xs">{FINDINGS_DIFFER}</p>
      ) : null}
      {truncated.map((words) => (
        <p key={words} className="text-ink-dim text-xs">
          {words}
        </p>
      ))}
    </section>
  );
}

function EntryLine({ entry, zone, now }: { entry: StripEntry; zone: string; now: number }) {
  const at = (iso: string) => formatZonedTime(iso, { zone, now });
  switch (entry.kind) {
    case "finding":
      return <FindingLine finding={entry.finding} ended={entry.finding.closed_at} at={at} />;
    case "incident":
      return (
        <span className="text-ink">
          <span
            className={
              entry.incident.severity === "critical" ? "text-critical-text" : "text-warning-text"
            }
          >
            Alert
          </span>
          {` · ${visibleEscapes(entry.incident.rule_name)} on ${visibleEscapes(entry.incident.service_name)} since ${at(entry.incident.opened_at)}`}
          {entry.incident.resolved_at ? `, ended ${at(entry.incident.resolved_at)}` : ""}
        </span>
      );
    case "release":
      return (
        <span className="text-ink-dim">
          {`${visibleEscapes(entry.mark.service_name)} ${visibleEscapes(entry.mark.version)} `}
          {entry.mark.basis === "seen" ? "first seen in telemetry " : "at "}
          {at(entry.mark.at)}
        </span>
      );
  }
}

function FindingLine({
  finding,
  ended,
  at,
}: {
  finding: ContextFinding;
  ended: string | null;
  at: (iso: string) => string;
}) {
  return (
    <span className="text-ink">
      {/* The severity in a word as well as colour (design principle 6). */}
      <span
        className={finding.severity === "critical" ? "text-critical-text" : "text-warning-text"}
      >
        {finding.severity === "critical" ? "Critical" : "Warning"}
      </span>{" "}
      {/* Followed on purpose, so the project and window shown here go with it (AC65). */}
      <Link
        to="/findings/$findingId"
        params={{ findingId: finding.id }}
        onClick={adoptScreenOverride}
        className="inline-flex min-h-11 items-center underline md:min-h-0"
      >
        {visibleEscapes(finding.title)}
      </Link>
      {ended ? <span className="text-ink-dim">{` · ended ${at(ended)}`}</span> : null}
    </span>
  );
}
