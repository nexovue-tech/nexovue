import { Link } from "@tanstack/react-router";

import { formatRelativeTime } from "../format/time";
import type { ProfiledService } from "./api";
import { PROFILING_SETUP_URL, PreviewLabel } from "./preview";

/** Profile types in the strip's few words; the Profiles screen has the full names. */
const SHORT_TYPE_NAMES: Record<string, string> = {
  cpu: "CPU",
  samples: "CPU",
  wall: "wall time",
  off_cpu: "off-CPU",
  alloc_space: "allocations",
  alloc_objects: "allocations",
  allocated_space: "allocations",
  allocated_objects: "allocations",
  inuse_space: "memory",
  inuse_objects: "memory",
  contentions: "locks",
  delay: "locks",
  goroutine: "goroutines",
  goroutines: "goroutines",
};

/**
 * A service's profiling at a glance (F17, FR11): whether it sent profiles in the
 * window, when it last did, which kinds, and the way into them — from the page a
 * person is already on.
 */
export function ProfilingStrip({
  serviceName,
  profiled,
  phrase,
}: {
  serviceName: string;
  /** The service's entry among the window's profiled services; undefined when absent. */
  profiled: ProfiledService | undefined;
  phrase: string;
}) {
  const kinds = [
    ...new Set(
      (profiled?.types ?? []).map((type) => SHORT_TYPE_NAMES[type.sample_type] ?? type.sample_type),
    ),
  ];

  return (
    <section
      aria-label="Profiling"
      className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-line-subtle border-b py-2 text-sm"
    >
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Profiling</span>
      {profiled ? (
        <>
          <span className="text-ink-dim">
            Profiled — {kinds.join(", ")}, last received{" "}
            {formatRelativeTime(profiled.last_received)}.
          </span>
          <Link
            to="/profiles"
            search={{ service: serviceName }}
            className="font-medium text-brand-text hover:underline"
          >
            Open profile
          </Link>
        </>
      ) : (
        <>
          <span className="text-ink-dim">No profiles {phrase}.</span>
          <a href={PROFILING_SETUP_URL} className="font-medium text-brand-text hover:underline">
            Set up profiling
          </a>
        </>
      )}
      <PreviewLabel />
    </section>
  );
}
