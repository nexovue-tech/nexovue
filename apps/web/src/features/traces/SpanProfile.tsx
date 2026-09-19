import { Link } from "@tanstack/react-router";

import { useSpanProfile } from "@/shared/profiles/api";
import { PROFILING_SETUP_URL, PreviewLabel } from "@/shared/profiles/preview";
import { setAbsoluteWindow, useSelection } from "@/shared/selection";

import type { Span } from "./api";

/**
 * A span's profile, when the profiler recorded samples while the span was running
 * (F17, FR10). When none landed in it, the section says which of two things is true,
 * because they are acted on differently: the service is profiled and this span was
 * too short to catch a sample, or the service is not profiled at all.
 */
/**
 * The minutes around a span that its profile is read over — the same neighbourhood
 * the API judges "profiled" in (`profiles/service.py`, SPAN_NEIGHBOURHOOD).
 */
const NEIGHBOURHOOD_MS = 5 * 60_000;

export function SpanProfile({ traceId, span }: { traceId: string; span: Span }) {
  const { projectId } = useSelection();
  const started = Date.parse(span.start_time);
  const end = new Date(started + Math.max(span.duration_ms, 1)).toISOString();
  // The Profiles screen reads the console's window, not the link; without this, an
  // older span opens on the last hour and contradicts the link just followed.
  const openAroundTheSpan = () =>
    setAbsoluteWindow(
      new Date(started - NEIGHBOURHOOD_MS).toISOString(),
      new Date(Date.parse(end) + NEIGHBOURHOOD_MS).toISOString(),
    );
  const profile = useSpanProfile(projectId, {
    service: span.service_name,
    traceId,
    spanId: span.span_id,
    from: span.start_time,
    to: end,
  });

  if (profile.isPending || profile.isError) return null;
  const { state, linked_samples: samples, types } = profile.data;

  return (
    <section aria-label="Profile" className="mb-5">
      <h3 className="mb-1.5 flex items-center justify-between border-line-subtle border-b pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        Profile
        <PreviewLabel />
      </h3>
      {state === "linked" ? (
        <Link
          to="/profiles"
          search={{
            service: span.service_name,
            type: types[0],
            trace: traceId,
            span: span.span_id,
          }}
          onClick={openAroundTheSpan}
          className="text-brand-text text-sm hover:underline"
        >
          Open this span's profile — {samples.toLocaleString("en")}{" "}
          {samples === 1 ? "sample" : "samples"}
        </Link>
      ) : state === "no_samples" ? (
        <p className="text-ink-dim text-sm">
          {span.service_name} was profiled, but no sample landed in this span — a span shorter than
          the sampling interval (about 50 ms) rarely catches one, and most profilers do not yet link
          samples to spans.{" "}
          <Link
            to="/profiles"
            search={{ service: span.service_name }}
            onClick={openAroundTheSpan}
            className="text-brand-text hover:underline"
          >
            Open the service's profile
          </Link>
        </p>
      ) : (
        <p className="text-ink-dim text-sm">
          {span.service_name} isn't profiled.{" "}
          <a href={PROFILING_SETUP_URL} className="text-brand-text hover:underline">
            Set up profiling
          </a>
        </p>
      )}
    </section>
  );
}
