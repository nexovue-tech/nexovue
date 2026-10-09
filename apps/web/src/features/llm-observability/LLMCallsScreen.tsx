import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/shared/components/Button";
import type { Column } from "@/shared/components/DataTable";
import { DataTable } from "@/shared/components/DataTable";
import { EmptyState } from "@/shared/components/EmptyState";
import type { FilterValues } from "@/shared/components/FilterBar";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { filterParams, withFilter } from "@/shared/filters";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, NUMBER_LOCALE, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";

import type { LLMCallSummary } from "./api";
import { useLLMCalls } from "./api";
import { llmCallFilters, llmCallsNarrowed, MODEL_KEY, SERVICE_KEY } from "./filters";

/** Typing a character must not send a request; the pause before the next one may. */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * Every model call this project reported — one row per service, span name and model.
 *
 * **No drill-down screen.** A row's own span already exists in the trace waterfall, so
 * its service and span name link into the existing Traces screen narrowed to it, rather
 * than this feature building a second detail view for AC the spec's own scope defers
 * (agent-trajectory views).
 *
 * Both filters go to the server: `service`/`model` are substring matches over values
 * this screen never enumerates in advance, so there is nothing to offer in a closed-set
 * `FilterBar` the way `metrics`'s shape filter can. See `filters.ts`.
 */
export function LLMCallsScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();
  const navigate = useNavigate();

  const search = useSearch({ from: "/shell/llm" });
  const filters = useMemo(() => llmCallFilters(search), [search]);

  // `replace`: narrowing this list is browsing, and the back button belongs to
  // the screen somebody arrived from rather than to every search they tried.
  const show = (next: FilterValues) =>
    void navigate({ to: "/llm", search: () => filterParams(next), replace: true });

  const [typedService, onTypedService] = useDebouncedFilter(filters[SERVICE_KEY] ?? "", (value) =>
    show(withFilter(filters, SERVICE_KEY, value)),
  );
  const [typedModel, onTypedModel] = useDebouncedFilter(filters[MODEL_KEY] ?? "", (value) =>
    show(withFilter(filters, MODEL_KEY, value)),
  );

  const calls = useLLMCalls(projectId, timeWindow, filterParams(filters));

  return (
    <LLMCallsView
      windowLabel={windowPhrase}
      calls={calls.data?.calls ?? []}
      isPending={calls.isPending}
      isError={calls.isError}
      error={calls.error}
      onRetry={() => void calls.refetch()}
      typedService={typedService}
      onTypedService={onTypedService}
      typedModel={typedModel}
      onTypedModel={onTypedModel}
      // A search box counts as narrowing the moment it is typed, not when it
      // reaches the URL — otherwise the empty state reached by typing blames
      // the project for a filter the reader can see in the box.
      narrowed={llmCallsNarrowed(filters) || typedService !== "" || typedModel !== ""}
      onClearFilters={() => show({})}
    />
  );
}

/**
 * The value being typed, handed to the URL once typing stops.
 *
 * Held here rather than read back out of the address bar, for the same reason
 * `MetricsScreen.tsx`'s own `useTypedName` is: the router applies a navigation
 * inside a React transition, and a text input rendered from state a transition
 * owns shows the value it had before the keystroke.
 */
function useDebouncedFilter(
  urlValue: string,
  onSettled: (value: string | undefined) => void,
): [string, (value: string) => void] {
  const [typed, setTyped] = useState(urlValue);
  const [knownUrlValue, setKnownUrlValue] = useState(urlValue);
  const settled = useDebounced(typed, SEARCH_DEBOUNCE_MS);
  const sent = useRef(urlValue);

  const commit = useRef(onSettled);
  useEffect(() => {
    commit.current = onSettled;
  });

  if (urlValue !== knownUrlValue) {
    setKnownUrlValue(urlValue);
    setTyped(urlValue);
    sent.current = urlValue;
  }

  useEffect(() => {
    if (settled === sent.current) return;
    sent.current = settled;
    commit.current(settled || undefined);
  }, [settled]);

  return [typed, setTyped];
}

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}

export interface LLMCallsViewProps {
  windowLabel: string;
  calls: readonly LLMCallSummary[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  typedService: string;
  onTypedService: (value: string) => void;
  typedModel: string;
  onTypedModel: (value: string) => void;
  narrowed: boolean;
  onClearFilters: () => void;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function LLMCallsView({
  windowLabel,
  calls,
  isPending,
  isError,
  error,
  onRetry,
  typedService,
  onTypedService,
  typedModel,
  onTypedModel,
  narrowed,
  onClearFilters,
}: LLMCallsViewProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">LLM calls</h1>
        <p className="mt-1 text-ink-dim text-sm">
          {narrowed
            ? `The model calls matching this search, of everything reported over ${windowLabel}.`
            : `Every model call this project reported over ${windowLabel}, busiest first.`}{" "}
          A span with no model name groups under "unlabeled model" rather than being dropped.
        </p>
      </header>

      <div className="flex shrink-0 flex-wrap gap-3 px-4 pb-3">
        <input
          type="search"
          value={typedService}
          onChange={(event) => onTypedService(event.target.value)}
          placeholder="Search services"
          aria-label="Search services"
          className="w-full max-w-xs rounded-sm border border-line bg-surface px-2.5 py-1 font-mono text-ink text-xs outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
        />
        <input
          type="search"
          value={typedModel}
          onChange={(event) => onTypedModel(event.target.value)}
          placeholder="Search models"
          aria-label="Search models"
          className="w-full max-w-xs rounded-sm border border-line bg-surface px-2.5 py-1 font-mono text-ink text-xs outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
        />
      </div>

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={8} label="Loading the model calls" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure title="Model calls could not be loaded" error={error} onRetry={onRetry} />
        </div>
      ) : calls.length === 0 ? (
        <EmptyCalls narrowed={narrowed} onClearFilters={onClearFilters} />
      ) : (
        <CallsTable calls={calls} />
      )}
    </div>
  );
}

/**
 * Why the list is empty, which is two situations and not one.
 *
 * "Nothing matched what you searched for" and "this project has never reported
 * a model call" ask opposite things of the reader.
 */
function EmptyCalls({
  narrowed,
  onClearFilters,
}: {
  narrowed: boolean;
  onClearFilters: () => void;
}) {
  if (narrowed) {
    return (
      <EmptyState
        title="No model calls match this search"
        detail="Nothing this project reported over this period matches what you searched for. Clear the search, or widen the time range."
        action={
          <Button variant="secondary" onClick={onClearFilters}>
            Clear search
          </Button>
        }
      />
    );
  }

  return (
    <EmptyState
      title="No model calls seen in this window"
      detail="Nothing this project reported over this period carried a gen_ai.* attribute, or widen the time range if you expected something older."
    />
  );
}

function CallsTable({ calls }: { calls: readonly LLMCallSummary[] }) {
  const columns: ReadonlyArray<Column<LLMCallSummary>> = [
    {
      key: "service",
      header: "Service",
      cell: (call) => <span className="text-ink">{call.service_name}</span>,
    },
    {
      key: "span",
      header: "Span",
      // The one way into this group's own spans: the trace waterfall already
      // has the detail, so clicking here narrows it rather than this feature
      // building a second view of the same data.
      cell: (call) => (
        <Link
          to="/traces"
          search={{ service: call.service_name, operation: call.span_name }}
          className="text-brand-text hover:underline"
          title="Open these spans in Traces"
        >
          {call.span_name}
        </Link>
      ),
    },
    {
      key: "model",
      header: "Model",
      cell: (call) => (
        <span className={call.model_name === "unlabeled model" ? "text-ink-dim" : "text-ink"}>
          {call.model_name}
        </span>
      ),
    },
    { key: "calls", header: "Calls", numeric: true, cell: (call) => formatCount(call.call_count) },
    {
      key: "p50",
      header: "p50",
      numeric: true,
      cell: (call) => formatDuration(call.p50_ms),
    },
    {
      key: "p95",
      header: "p95",
      numeric: true,
      cell: (call) => formatDuration(call.p95_ms),
    },
    {
      key: "errors",
      header: "Errors",
      numeric: true,
      cell: (call) => formatCount(call.error_count),
    },
    {
      key: "input_tokens",
      header: "Input tokens",
      numeric: true,
      cell: (call) => formatCount(call.input_tokens),
    },
    {
      key: "output_tokens",
      header: "Output tokens",
      numeric: true,
      cell: (call) => formatCount(call.output_tokens),
    },
    {
      // Always an em dash today — see `llm_observability/queries.py`'s
      // `GEN_AI_COST_ATTRIBUTE`. Kept as a real column, not hidden, so cost
      // appears the day a plan step points that constant at real evidence.
      key: "cost",
      header: "Cost",
      numeric: true,
      cell: (call) => (call.cost === null ? UNKNOWN : call.cost.toLocaleString(NUMBER_LOCALE)),
    },
  ];

  return (
    <section className="flex min-h-0 flex-1 flex-col border-line border-t px-4">
      <div className="flex items-baseline justify-between py-2">
        <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">
          {formatCount(calls.length)} {plural(calls.length, "group")}
        </h2>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <DataTable
          caption="Model calls this project reported, grouped by service, span and model"
          rows={calls}
          rowKey={(call) => `${call.service_name}:${call.span_name}:${call.model_name}`}
          empty="No model calls."
          columns={columns}
        />
      </div>
    </section>
  );
}
