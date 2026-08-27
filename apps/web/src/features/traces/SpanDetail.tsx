import { X } from "lucide-react";
import type { ReactNode } from "react";

import { formatDuration } from "@/shared/format/duration";
import { formatClockTime, formatDate } from "@/shared/format/time";

import { httpStatusOf, normalizeStatus, spanFailed } from "./api";
import type { WaterfallRow } from "./trace-tree";

/**
 * Everything about one span that a row has no room for.
 *
 * The row answers "where did the time go"; this answers "what actually happened" —
 * which is the status message, the HTTP detail and the attributes the service attached.
 */
export function SpanDetail({ row, onClose }: { row: WaterfallRow; onClose: () => void }) {
  const { span } = row;
  const failed = spanFailed(span);
  const httpStatus = httpStatusOf(span);
  const attributes = flattenAttributes(span.attributes ?? {});

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
        {failed && span.status_message ? (
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
        </Section>

        {span.http_method || span.http_route || httpStatus !== undefined ? (
          <Section title="HTTP">
            {span.http_method ? <Entry term="Method" value={span.http_method} mono /> : null}
            {span.http_route ? <Entry term="Route" value={span.http_route} mono /> : null}
            {httpStatus !== undefined ? (
              <Entry term="Status" value={String(httpStatus)} mono />
            ) : null}
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
