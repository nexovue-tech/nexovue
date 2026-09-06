import { Link } from "@tanstack/react-router";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { CopyButton } from "@/shared/components/CopyButton";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { formatCount } from "@/shared/format/number";
import { formatClockTime, formatTimestamp } from "@/shared/format/time";

import type { ErrorGroupDetail, ErrorGroupSummary, ErrorOccurrence } from "./api";
import { GROUPED_BY_SDK } from "./api";
import {
  describeOccurrences,
  describeServices,
  firstWithStacktrace,
  stackFrames,
} from "./error-groups";
import type { SymbolicatedFrame, SymbolicationState } from "./symbolication";
import { identified } from "./symbolication";

/**
 * One exception in full: what it is, how long it has been happening, and the stack.
 *
 * The list answers "what is failing and how often". This answers "what actually
 * threw" — which is the stack trace, and the instances it was read from.
 */
export function ErrorGroupPanel({
  summary,
  detail,
  symbolication,
  isPending,
  isError,
  error,
  onRetry,
  onClose,
}: {
  /** The list's own row, so the panel has an identity before the read returns. */
  summary: ErrorGroupSummary;
  /** The resolved stack, already asked for by the route component. */
  symbolication: SymbolicationState;
  detail?: ErrorGroupDetail;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  onClose: () => void;
}) {
  const group = detail?.group ?? summary;
  const recent = detail?.recent ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start gap-3 border-line border-b bg-surface px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
            {describeServices(group.services)}
          </p>
          <p className="mt-1 break-words font-medium text-critical-text">{group.exception_type}</p>
          <p className="mt-1 break-words text-ink-dim text-sm">{group.message_template}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close error detail"
          className="shrink-0 rounded-xs p-1 text-ink-dim hover:text-ink"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <Section title="How often">
          <dl>
            <Entry
              term="Occurrences"
              value={formatCount(group.occurrences)}
              mono
              hint="De-duplicated: one throw recorded on both a span and a log record is one occurrence."
            />
            <Entry term="First seen" value={formatTimestamp(group.first_seen)} />
            <Entry term="Last seen" value={formatTimestamp(group.last_seen)} />
            <Entry term="Services" value={group.services.join(", ") || "—"} />
            <Entry
              term="Grouped by"
              value={
                group.grouped_by === GROUPED_BY_SDK
                  ? "your SDK's fingerprint"
                  : "exception type and message"
              }
            />
          </dl>
        </Section>

        {/* The template is the group; the message is what one instance said. Showing
            both only earns its space when the tokens actually collapsed something. */}
        {group.latest_message && group.latest_message !== group.message_template ? (
          <Section title="Latest message">
            <p className="break-words font-mono text-ink text-xs">{group.latest_message}</p>
          </Section>
        ) : null}

        {isPending ? (
          <SkeletonRows rows={4} label="Loading occurrences" />
        ) : isError ? (
          <InlineFailure title="Instances could not be loaded" error={error} onRetry={onRetry} />
        ) : (
          <>
            <StackTrace occurrences={recent} symbolication={symbolication} />
            <RecentOccurrences occurrences={recent} />
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The stack, in a box of its own.
 *
 * A stack trace is fifty lines long and two hundred columns wide, so it is bounded in
 * both directions: it scrolls vertically inside a fixed height rather than pushing
 * every fact above it off the panel, and each frame wraps under a hanging indent
 * rather than making a reader scroll sideways once per line.
 */
function StackTrace({
  occurrences,
  symbolication,
}: {
  occurrences: readonly ErrorOccurrence[];
  symbolication: SymbolicationState;
}) {
  const carrier = firstWithStacktrace(occurrences);

  if (!carrier) {
    return (
      <Section title="Stack trace">
        <p className="text-ink-dim text-sm leading-relaxed">
          None of the recorded instances carried one. An exception logged as a message rather than
          raised through an SDK's exception recorder arrives with a type and a message and nothing
          else — open the trace below to see where it happened.
        </p>
      </Section>
    );
  }

  const resolved = symbolication.frames?.filter((frame) => frame.resolved) ?? [];

  return (
    <Section
      title="Stack trace"
      aside={<CopyButton value={carrier.stacktrace} label="stack trace" />}
    >
      <p className="mb-1.5 text-ink-dim text-xs">
        From the instance at {formatClockTime(carrier.occurred_at)} on {carrier.service_name}.
      </p>

      {resolved.length > 0 ? (
        <OriginalStack frames={symbolication.frames ?? []} resolvedCount={resolved.length} />
      ) : (
        <>
          <pre className="max-h-80 overflow-y-auto rounded-xs bg-surface p-3 font-mono text-2xs text-ink leading-relaxed">
            {stackFrames(carrier.stacktrace).map((frame) => (
              <span key={frame.id} className="-indent-6 block whitespace-pre-wrap break-words pl-6">
                {frame.text}
              </span>
            ))}
          </pre>
          <WhyStillMinified
            release={carrier.release}
            pending={symbolication.isPending}
            failed={symbolication.isError}
            answered={symbolication.frames !== undefined}
          />
        </>
      )}
    </Section>
  );
}

/**
 * The stack after symbolication, one frame per line.
 *
 * A frame no map covered keeps its minified position rather than being dropped —
 * a trace missing its middle is harder to read than one partly minified — and is
 * dimmed so the eye goes to the frames that are actually source.
 */
function OriginalStack({
  frames,
  resolvedCount,
}: {
  frames: readonly SymbolicatedFrame[];
  resolvedCount: number;
}) {
  return (
    <>
      <pre className="max-h-80 overflow-y-auto rounded-xs bg-surface p-3 font-mono text-2xs leading-relaxed">
        {identified(frames).map(({ id, frame }) => (
          <span
            key={id}
            className={`-indent-6 block whitespace-pre-wrap break-words pl-6 ${
              frame.resolved ? "text-ink" : "text-ink-dim"
            }`}
          >
            {"    at "}
            {frame.function_name === "" ? "" : `${frame.function_name} `}
            {`${frame.file}:${frame.line}:${frame.column}`}
            {frame.context_line === "" ? null : (
              <span className="block pl-6 text-ink-dim">{frame.context_line.trim()}</span>
            )}
          </span>
        ))}
      </pre>
      <p className="mt-1.5 text-ink-dim text-xs">
        {resolvedCount} of {frames.length} frames mapped back to source. A function name needs the
        minified bundle beside the map, which is not stored — the file and line do not.
      </p>
    </>
  );
}

/**
 * Why the stack above is still minified, when it is.
 *
 * Silence here reads as the product being broken. Each branch names the one
 * thing the customer would have to change.
 */
function WhyStillMinified({
  release,
  pending,
  failed,
  answered,
}: {
  release: string;
  pending: boolean;
  failed: boolean;
  answered: boolean;
}) {
  if (pending) return <p className="mt-1.5 text-ink-dim text-xs">Looking for a source map…</p>;
  if (failed) {
    return (
      <p className="mt-1.5 text-ink-dim text-xs">
        The source map lookup failed, so this stack is shown as it arrived.
      </p>
    );
  }
  if (release.trim() === "") {
    return (
      <p className="mt-1.5 text-ink-dim text-xs">
        This instance carries no release, so there is nothing to match a source map against. Set{" "}
        <code className="font-mono text-2xs">serviceVersion</code> in the browser SDK, and upload
        that build&apos;s maps.
      </p>
    );
  }
  if (answered) {
    return (
      <p className="mt-1.5 text-ink-dim text-xs">
        No uploaded source map covers these files for release{" "}
        <code className="font-mono text-2xs">{release}</code>.
      </p>
    );
  }
  return null;
}

/** The instances behind the count, newest first, each a way into its trace. */
function RecentOccurrences({ occurrences }: { occurrences: readonly ErrorOccurrence[] }) {
  if (occurrences.length === 0) return null;

  return (
    <Section title={`Recent instances (${occurrences.length})`}>
      <ul>
        {describeOccurrences(occurrences).map(({ id, occurrence }) => (
          <li
            key={id}
            className="flex items-baseline gap-3 border-line-subtle border-b py-1 text-sm last:border-b-0"
          >
            <span className="w-24 shrink-0 font-mono text-2xs text-ink-dim">
              {formatClockTime(occurrence.occurred_at)}
            </span>
            <span className="min-w-0 flex-1 truncate text-ink-dim">{occurrence.service_name}</span>
            {occurrence.trace_id ? (
              <Link
                to="/traces/$traceId"
                params={{ traceId: occurrence.trace_id }}
                className="shrink-0 font-mono text-2xs text-brand-text underline-offset-2 hover:underline"
              >
                trace
              </Link>
            ) : (
              // Not a broken link: an uninstrumented logger's record has no trace to open.
              <span className="shrink-0 font-mono text-2xs text-ink-dim">no trace</span>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Section({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-5">
      <div className="mb-1.5 flex items-center justify-between gap-3 border-line-subtle border-b pb-1">
        <h3 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Entry({
  term,
  value,
  mono = false,
  hint,
}: {
  term: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <div className="flex gap-3 py-0.5 text-sm">
      <dt className="w-24 shrink-0 truncate text-ink-dim" title={term}>
        {term}
      </dt>
      <dd
        className={`min-w-0 flex-1 break-words text-ink ${mono ? "font-mono text-xs" : ""}`}
        title={hint}
      >
        {value}
      </dd>
    </div>
  );
}
