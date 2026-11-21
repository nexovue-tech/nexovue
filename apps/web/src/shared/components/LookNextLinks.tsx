import { Link } from "@tanstack/react-router";

import type { LookNextEntry } from "@/shared/evidence-links";
import { setSelectedProjectId } from "@/shared/selection";

const LINK = "font-medium text-brand-text text-sm underline-offset-2 hover:underline";
/** Below `md`, a link is at least 44 pixels high (F3 NFR4). */
const TAP = "inline-flex min-h-11 items-center md:min-h-0";

/**
 * "Look next" (F3 FR42, FR53): links built by `evidence-links.ts` from a finding or an
 * incident, each opening its data at that window, or saying the raw data aged out
 * (FR17). Following one selects the project it is about, as `ServiceLink` does.
 */
export function LookNextLinks({
  entries,
  projectId,
  headingId,
}: {
  entries: readonly LookNextEntry[];
  projectId: string;
  headingId: string;
}) {
  return (
    <nav aria-labelledby={headingId} className="flex flex-col gap-1">
      <h3 id={headingId} className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
        Look next
      </h3>
      <ul className="flex flex-col gap-x-4 md:flex-row md:flex-wrap">
        {entries.map((entry) => (
          <li key={entry.label}>
            {entry.kind === "link" ? (
              <Link
                to={entry.to}
                params={entry.params}
                search={entry.search}
                onClick={() => setSelectedProjectId(projectId)}
                className={`${LINK} ${TAP}`}
              >
                {entry.label}
              </Link>
            ) : (
              <span className={`text-ink-dim text-sm ${TAP}`}>{entry.label}</span>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** One fact's own link, or its aged-out line, inline beside the fact (FR16, FR17). */
export function FactLink({ entry, projectId }: { entry: LookNextEntry; projectId: string }) {
  if (entry.kind === "aged_out") {
    return <span className="text-ink-dim text-sm">{entry.label}</span>;
  }
  return (
    <Link
      to={entry.to}
      params={entry.params}
      search={entry.search}
      onClick={() => setSelectedProjectId(projectId)}
      className={`${LINK} ${TAP} [overflow-wrap:anywhere]`}
    >
      {entry.label}
    </Link>
  );
}
