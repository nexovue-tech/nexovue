import { Link } from "@tanstack/react-router";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { formatDuration } from "@/shared/format/duration";
import { formatClockTime, formatDate } from "@/shared/format/time";

import type { SpanLink } from "./api";
import { httpStatusOf, normalizeStatus, spanFailed } from "./api";
import { SpanProfile } from "./SpanProfile";
import type { WaterfallRow } from "./trace-tree";

/**
 * Everything about one span that a row has no room for.
 *
 * The row answers "where did the time go"; this answers "what actually happened" —
 * which is the status message, the HTTP or RPC detail, and the attributes the service
 * attached. A span has one of those two, never both.
 */
export function SpanDetail({
  traceId,
  row,
  onClose,
}: {
  traceId: string;
  row: WaterfallRow;
  onClose: () => void;
}) {
  const { span } = row;
  const failed = spanFailed(span);
  const httpStatus = httpStatusOf(span);
  const attributes = flattenAttributes(span.attributes ?? {});
  const events = span.events ?? [];
  const links = span.links ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start gap-3 border-line border-b bg-surface px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
            {span.service_name}
          </p>
          <p
            className={`mt-1 break-words font-medium ${failed ? "text-critical-text" : "text-ink"}`}
          >
            {span.span_name}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close span detail"
          className="shrink-0 rounded-xs p-1 text-ink-dim hover:text-ink"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {/* What was thrown leads the pane, above everything including the
            status message — the type and the message are the two things a
            person opened this span to read, and the status message is often
            the same sentence with less in it. A span that failed without
            throwing has no exception, and falls back to the message. */}
        {span.exception_type || span.exception_message ? (
          <div className="mb-4 border-critical-solid border-l-2 py-1 pl-3">
            {span.exception_type ? (
              <p className="font-medium font-mono text-critical-text text-sm">
                {span.exception_type}
              </p>
            ) : null}
            {span.exception_message ? (
              <p className="mt-0.5 break-words text-ink text-sm">{span.exception_message}</p>
            ) : null}
          </div>
        ) : failed && span.status_message ? (
          <p className="mb-4 border-critical-solid border-l-2 py-1 pl-3 text-critical-text text-sm">
            {span.status_message}
          </p>
        ) : null}

        {row.orphaned ? <Note>The span this one belongs to is not in this trace.</Note> : null}
        {row.precedesParent ? (
          <Note>This span starts before its parent — the two clocks disagree.</Note>
        ) : null}

        <Section title="Timing">
          <Entry term="Took" value={formatDuration(span.duration_ms)} mono />
          <Entry term="Started" value={formatClockTime(span.start_time)} mono />
          <Entry term="On" value={formatDate(span.start_time)} />
        </Section>

        <Section title="Span">
          <Entry term="Kind" value={readableKind(span.span_kind)} />
          <Entry term="Status" value={readableStatus(span.status_code)} />
          <Entry term="Depth" value={String(row.depth)} mono />
          <Entry term="Id" value={span.span_id} mono selectable />
          {span.parent_span_id ? (
            <Entry term="Parent" value={span.parent_span_id} mono selectable />
          ) : null}
          {/* Which build ran this span. It answers "did a deploy cause this"
              for one span without leaving the trace, and is absent rather than
              blank when the service did not say. */}
          {span.deployment_version ? (
            <Entry term="Build" value={span.deployment_version} mono selectable />
          ) : null}
          {span.is_model_call ? <Entry term="Model call" value={modelNameOf(attributes)} /> : null}
        </Section>

        <SpanProfile traceId={traceId} span={span} />

        {span.http_method || span.http_route || httpStatus !== undefined ? (
          <Section title="HTTP">
            {span.http_method ? <Entry term="Method" value={span.http_method} mono /> : null}
            {span.http_route ? <Entry term="Route" value={span.http_route} mono /> : null}
            {httpStatus !== undefined ? (
              <Entry term="Status" value={String(httpStatus)} mono />
            ) : null}
          </Section>
        ) : null}

        {span.rpc_method || span.rpc_status || span.rpc_system ? (
          <Section title="RPC">
            {span.rpc_method ? (
              <Entry term="Method" value={span.rpc_method} mono selectable />
            ) : null}
            {span.rpc_status ? <Entry term="Status" value={span.rpc_status} mono /> : null}
            {span.rpc_system ? <Entry term="System" value={span.rpc_system} mono /> : null}
          </Section>
        ) : null}

        {/* What happened inside the span, offset from its start rather than at
            an absolute clock time: the span's own start is the only reference
            a reader has here, and "+312ms" answers "when in the request" where
            a timestamp makes them subtract. */}
        {events.length > 0 ? (
          <Section title="Timeline">
            {events.map((event) => (
              <Entry
                key={`${event.at}:${event.name}`}
                term={formatOffset(event.at, span.start_time)}
                value={event.name}
                mono
              />
            ))}
          </Section>
        ) : null}

        {/* A link points at a span in *another* trace — a queue consumer back
            at its producer. Rendered as navigation because that is what it is
            for; a trace we no longer hold answers "not in this window" on
            arrival rather than failing, which is what makes following one
            safe. */}
        {links.length > 0 ? (
          <Section title="Links">
            {links.map((link) => (
              <div key={`${link.trace_id}:${link.span_id}`} className="flex gap-3 py-0.5">
                <span className="w-20 shrink-0 text-2xs text-ink-dim">
                  {readableLinkKind(link)}
                </span>
                <Link
                  to="/traces/$traceId"
                  params={{ traceId: link.trace_id }}
                  className="min-w-0 break-all font-mono text-brand text-xs hover:underline"
                >
                  {link.trace_id}
                </Link>
              </div>
            ))}
          </Section>
        ) : null}

        {attributes.length > 0 ? (
          <Section title="Attributes">
            {attributes.map(([key, value]) => (
              <Entry key={key} term={key} value={value} mono selectable />
            ))}
          </Section>
        ) : null}
      </div>
    </div>
  );
}

/**
 * How far into the span an event happened.
 *
 * Relative rather than absolute: an event's position inside the span is what
 * the timeline exists to show, and a reader given two wall-clock times has to
 * subtract them to get it. An event outside the span — clock skew between the
 * SDK and the collector — falls back to the clock time rather than printing a
 * negative offset that reads as a bug in us.
 */
function formatOffset(at: string, spanStart: string): string {
  const offset = Date.parse(at) - Date.parse(spanStart);
  if (!Number.isFinite(offset) || offset < 0) return formatClockTime(at);
  return `+${formatDuration(offset)}`;
}

/** What a link is, when the producer said. "Linked" when it did not. */
function readableLinkKind(link: SpanLink): string {
  const kind = link.attributes?.kind;
  return typeof kind === "string" && kind ? kind : "linked";
}

function Note({ children }: { children: string }) {
  return (
    <p className="mb-3 border-warning-solid border-l-2 py-1 pl-3 text-sm text-warning-text">
      {children}
    </p>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-5">
      <h3 className="mb-1.5 border-line-subtle border-b pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        {title}
      </h3>
      <dl>{children}</dl>
    </section>
  );
}

function Entry({
  term,
  value,
  mono = false,
  selectable = false,
}: {
  term: string;
  value: string;
  mono?: boolean;
  selectable?: boolean;
}) {
  return (
    <div className="flex gap-3 py-0.5 text-sm">
      <dt className="w-24 shrink-0 truncate text-ink-dim" title={term}>
        {term}
      </dt>
      <dd
        className={`min-w-0 flex-1 break-words text-ink ${mono ? "font-mono text-xs" : ""} ${
          selectable ? "select-all" : ""
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

/** OTel spells these SPAN_KIND_CLIENT; a reader spells it client. */
function readableKind(kind: string): string {
  return kind.replace(/^SPAN_KIND_/, "").toLowerCase() || "unset";
}

function readableStatus(status: string): string {
  return normalizeStatus(status).toLowerCase() || "unset";
}

/**
 * Attributes arrive as nested objects — `http.request.method` is stored as three levels,
 * not one dotted key. Semantic-convention names are read as paths, so they are put back
 * together into one line each rather than shown as a JSON blob per top-level namespace.
 */
function flattenAttributes(attributes: Record<string, unknown>): Array<[string, string]> {
  const flat: Array<[string, string]> = [];

  function walk(value: unknown, path: string) {
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      const entries = Object.entries(value as Record<string, unknown>);
      if (entries.length > 0) {
        for (const [key, nested] of entries) walk(nested, path ? `${path}.${key}` : key);
        return;
      }
    }
    flat.push([path, readableValue(value)]);
  }

  walk(attributes, "");
  return flat.sort(([left], [right]) => left.localeCompare(right));
}

function readableValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** The flattened `gen_ai.request.model` attribute, or an explicit placeholder
 * when a model call did not name its model — mirrors the API's own
 * "unlabeled model" fallback (`llm_observability/service.py`). */
function modelNameOf(attributes: Array<[string, string]>): string {
  const match = attributes.find(([key]) => key === "gen_ai.request.model");
  return match ? match[1] : "unlabeled model";
}
