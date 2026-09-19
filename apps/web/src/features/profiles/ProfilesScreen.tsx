import { Link, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useState } from "react";

import { EmptyState } from "@/shared/components/EmptyState";
import { FilterBar } from "@/shared/components/FilterBar";
import type { FrameIdentity } from "@/shared/components/FlameGraph";
import { FlameGraph } from "@/shared/components/FlameGraph";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import { SkeletonRows } from "@/shared/components/Skeleton";
import type { PlotRows } from "@/shared/components/TrendChart";
import { TrendChart } from "@/shared/components/TrendChart";
import { formatRelativeTime, formatTimestamp } from "@/shared/format/time";
import type { ProfiledService } from "@/shared/profiles/api";
import { useProfiledServices } from "@/shared/profiles/api";
import { PROFILING_SETUP_URL, PreviewLabel } from "@/shared/profiles/preview";
import type { LiveWindow } from "@/shared/selection";
import {
  setAbsoluteWindow,
  useLiveWindow,
  useSelection,
  useWindowPhrase,
} from "@/shared/selection";
import type { FlameGraphResponse, GraphQuery } from "./api";
import { useFlameGraph, useLatestProfile, useProfileFilters, useProfileTimeline } from "./api";
import type { BaselineRange, CompareBy, CompareChoice } from "./CompareView";
import { CompareView } from "./CompareView";
import { FunctionsTable } from "./FunctionsTable";
import { apiValue, PROFILE_FILTER_KEYS, profileFilterDefinitions } from "./filters";
import { isStale, readingOf } from "./profile-reading";
import { formatValue, typeLabel, typeName, typePhrase } from "./profile-words";

/** What the URL carries, so a profile someone found can be shared. */
export interface ProfilesSearch {
  service?: string;
  type?: string;
  host?: string;
  version?: string;
  env?: string;
  /** A span's own samples only (FR10). */
  trace?: string;
  span?: string;
  /** Compare mode (FR9). */
  compare?: CompareBy;
  base?: string;
  against?: string;
  baseline?: BaselineRange;
}

const HEX_TRACE = /^[0-9a-f]{32}$/;
const HEX_SPAN = /^[0-9a-f]{16}$/;

export function parseProfilesSearch(search: Record<string, unknown>): ProfilesSearch {
  const named = (value: unknown) => (typeof value === "string" && value !== "" ? value : undefined);
  const matching = (value: unknown, pattern: RegExp) => {
    const text = named(value);
    return text !== undefined && pattern.test(text) ? text : undefined;
  };
  const oneOf = <T extends string>(value: unknown, allowed: readonly T[]) =>
    allowed.find((candidate) => candidate === value);
  const trace = matching(search.trace, HEX_TRACE);
  const span = matching(search.span, HEX_SPAN);
  return {
    service: named(search.service),
    type: named(search.type),
    host: named(search.host),
    version: named(search.version),
    env: named(search.env),
    // A span is named by both, or it is not named.
    ...(trace && span ? { trace, span } : {}),
    compare: oneOf(search.compare, ["versions", "ranges"] as const),
    base: named(search.base),
    against: named(search.against),
    baseline: oneOf(search.baseline, ["previous", "day", "week"] as const),
  };
}

/** The type a service is shown in first: CPU when it sent CPU, else the first it sent. */
function defaultType(service: ProfiledService): string | undefined {
  const cpu = service.types.find(
    (type) => type.sample_type === "cpu" || type.sample_type === "samples",
  );
  return cpu?.sample_type ?? service.types[0]?.sample_type;
}

/** A newest profile outside the window is shown in the hour that ends just after it. */
const AROUND_BEFORE_MS = 55 * 60_000;
const AROUND_AFTER_MS = 5 * 60_000;

function showAround(lastReceived: string) {
  const at = Date.parse(lastReceived);
  setAbsoluteWindow(
    new Date(at - AROUND_BEFORE_MS).toISOString(),
    new Date(at + AROUND_AFTER_MS).toISOString(),
  );
}

export function ProfilesScreen({ search }: { search: ProfilesSearch }) {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const phrase = useWindowPhrase();
  const navigate = useNavigate();
  const services = useProfiledServices(projectId, timeWindow);

  const listed = services.data?.services ?? [];
  const chosen =
    search.service === undefined
      ? listed[0]
      : listed.find((service) => service.service_name === search.service);
  const sampleType = chosen?.types.some((type) => type.sample_type === search.type)
    ? search.type
    : chosen && defaultType(chosen);

  const show = (next: Partial<ProfilesSearch>) =>
    void navigate({ to: "/profiles", search: (current) => ({ ...current, ...next }) });

  return (
    // The scroller is the containing block for what is absolutely placed inside it
    // (the functions table's screen-reader caption), so it scrolls with the rest.
    <div className="relative flex h-full min-h-0 flex-col gap-5 overflow-y-auto px-4 pt-5 pb-10">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Profiles</h1>
          <p className="mt-1 text-ink-dim text-sm">
            Which code each service spends its time and memory in, {phrase}.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {chosen && sampleType && !search.trace ? (
            <button
              type="button"
              aria-pressed={search.compare !== undefined}
              onClick={() => show({ compare: search.compare ? undefined : "versions" })}
              className="rounded-sm border border-line px-3 py-1 text-ink-dim text-sm hover:border-line-strong hover:text-ink"
            >
              {search.compare ? "Stop comparing" : "Compare"}
            </button>
          ) : null}
          <PreviewLabel />
        </div>
      </header>

      {services.isPending ? (
        <SkeletonRows rows={6} label="Loading profiled services" />
      ) : services.isError ? (
        <InlineFailure
          title="Profiled services could not be loaded"
          error={services.error}
          onRetry={() => void services.refetch()}
        />
      ) : chosen === undefined || sampleType === undefined ? (
        <NothingProfiled
          projectId={projectId}
          service={search.service}
          listed={listed}
          phrase={phrase}
          onPick={(service) => show({ service, type: undefined })}
        />
      ) : (
        <ProfileView
          // A new service, type or span is a new reading: its search, pick and zoom
          // must not carry over and dim or find frames of the one before.
          key={[chosen.service_name, sampleType, search.trace, search.span].join("|")}
          projectId={projectId}
          timeWindow={timeWindow}
          phrase={phrase}
          search={search}
          listed={listed}
          chosen={chosen}
          sampleType={sampleType}
          show={show}
        />
      )}
    </div>
  );
}

/**
 * The project has sent nothing in this window, or not from the service the link
 * named. Which one is said plainly, with the way to profiles that exist (BR6).
 */
function NothingProfiled({
  projectId,
  service,
  listed,
  phrase,
  onPick,
}: {
  projectId: string | undefined;
  service: string | undefined;
  listed: readonly ProfiledService[];
  phrase: string;
  onPick: (service: string) => void;
}) {
  const latest = useLatestProfile(projectId, service, true);
  const lastReceived = latest.data?.last_received ?? null;
  const setup = (
    <a href={PROFILING_SETUP_URL} className="font-medium text-brand-text text-sm hover:underline">
      Set up profiling
    </a>
  );

  if (latest.isPending) return <SkeletonRows rows={3} label="Looking for the newest profile" />;

  if (service !== undefined) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyState
          title={
            lastReceived ? `${service} sent no profiles ${phrase}` : `${service} isn't profiled`
          }
          detail={
            lastReceived
              ? `Its newest profile is from ${formatRelativeTime(lastReceived)}.`
              : "No profile from this service was received in the last 14 days. Profiles come from an OpenTelemetry Collector, with no code change."
          }
          action={
            lastReceived ? (
              <button
                type="button"
                onClick={() => showAround(lastReceived)}
                className="font-medium text-brand-text text-sm hover:underline"
              >
                Show the hour of its newest profile
              </button>
            ) : (
              setup
            )
          }
        />
        {listed.length > 0 ? (
          <p className="text-center text-ink-dim text-sm">
            Profiled {phrase}:{" "}
            {listed.slice(0, 5).map((other, index) => (
              <span key={other.service_name}>
                {index > 0 ? ", " : ""}
                <button
                  type="button"
                  onClick={() => onPick(other.service_name)}
                  className="text-brand-text hover:underline"
                >
                  {other.service_name}
                </button>
              </span>
            ))}
          </p>
        ) : null}
      </div>
    );
  }

  if (lastReceived) {
    return (
      <EmptyState
        title="No profiles in this range"
        detail={`The newest profile in this project is from ${formatRelativeTime(lastReceived)}.`}
        action={
          <button
            type="button"
            onClick={() => showAround(lastReceived)}
            className="font-medium text-brand-text text-sm hover:underline"
          >
            Show the hour of the newest profile
          </button>
        }
      />
    );
  }

  return (
    <EmptyState
      title="No profiles yet"
      detail="A profile shows which code a service spends its CPU and memory in. They come from an OpenTelemetry Collector running the eBPF profiler or the pprof receiver — no code change."
      action={setup}
    />
  );
}

function ProfileView({
  projectId,
  timeWindow,
  phrase,
  search,
  listed,
  chosen,
  sampleType,
  show,
}: {
  projectId: string | undefined;
  timeWindow: LiveWindow;
  phrase: string;
  search: ProfilesSearch;
  listed: readonly ProfiledService[];
  chosen: ProfiledService;
  sampleType: string;
  show: (next: Partial<ProfilesSearch>) => void;
}) {
  const [term, setTerm] = useState("");
  const [picked, setPicked] = useState<FrameIdentity | null>(null);

  const selection = { service: chosen.service_name, sampleType };
  const filters = useProfileFilters(projectId, timeWindow, selection);
  const graphQuery: GraphQuery = {
    ...selection,
    host: apiValue(search.host),
    version: apiValue(search.version),
    environment: apiValue(search.env),
    traceId: search.trace,
    spanId: search.span,
  };
  const narrowed =
    search.host !== undefined || search.version !== undefined || search.env !== undefined;
  const unit = chosen.types.find((type) => type.sample_type === sampleType)?.unit ?? "";

  const clearFilters = () => show({ host: undefined, version: undefined, env: undefined });

  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
        <SelectField
          capped
          label="Service"
          value={chosen.service_name}
          options={listed.map((service) => ({ id: service.service_name }))}
          optionLabel={(option) => option.id}
          onChange={(service) =>
            show({ service, type: undefined, host: undefined, version: undefined, env: undefined })
          }
        />
        <SelectField
          capped
          label="Profile"
          value={sampleType}
          options={chosen.types.map((type) => ({ id: type.sample_type, unit: type.unit }))}
          optionLabel={(option) => typeLabel(option.id, option.unit)}
          onChange={(type) => show({ type })}
        />
        <p className="text-ink-dim text-xs">
          Last received {formatRelativeTime(chosen.last_received)}
        </p>
      </div>

      {isStale(chosen.last_received) ? (
        // Stopped profiling and a quiet service must not look alike (US17.8).
        <p
          role="status"
          className="border-warning-solid border-l-2 py-0.5 pl-3 text-sm text-warning-text"
        >
          No profile from {chosen.service_name} since {formatTimestamp(chosen.last_received)} — its
          profiler may have stopped. What is below is what arrived before.
        </p>
      ) : null}

      {search.trace && search.span ? (
        <p
          role="status"
          className="flex flex-wrap items-baseline gap-x-3 rounded-sm border border-line px-3 py-2 text-sm"
        >
          <span className="text-ink">Only the samples recorded during one span.</span>
          <Link
            to="/traces/$traceId"
            params={{ traceId: search.trace }}
            className="text-brand-text hover:underline"
          >
            Back to the trace
          </Link>
          <button
            type="button"
            onClick={() => show({ trace: undefined, span: undefined })}
            className="text-brand-text hover:underline"
          >
            Show the whole service
          </button>
        </p>
      ) : null}

      {/* A span's own samples are already one host, one version: no filter narrows them. */}
      {search.trace ? null : (
        <>
          {/* FilterBar brings the gutter other screens need; this one already has it. */}
          <div className="-mx-4">
            <FilterBar
              definitions={profileFilterDefinitions(filters.data)}
              values={Object.fromEntries(PROFILE_FILTER_KEYS.map((key) => [key, search[key]]))}
              onChange={(key, value) => show({ [key]: value })}
              onClearAll={clearFilters}
            />
          </div>
        </>
      )}

      {search.compare && !search.trace ? (
        <CompareView
          projectId={projectId}
          selection={{
            ...selection,
            host: graphQuery.host,
            version: graphQuery.version,
            environment: graphQuery.environment,
          }}
          timeWindow={timeWindow}
          versions={{
            data: filters.data?.versions,
            isError: filters.isError,
            error: filters.error,
            refetch: () => void filters.refetch(),
          }}
          choice={{
            by: search.compare,
            baseVersion: search.base,
            version: search.against,
            baseline: search.baseline ?? "previous",
          }}
          onChoice={(next: Partial<CompareChoice>) =>
            show({
              ...("by" in next ? { compare: next.by } : {}),
              ...("baseVersion" in next ? { base: next.baseVersion } : {}),
              ...("version" in next ? { against: next.version } : {}),
              ...("baseline" in next ? { baseline: next.baseline } : {}),
            })
          }
          phrase={phrase}
        />
      ) : (
        <GraphSection
          evidence={
            <>
              {/* The timeline is the service's; a span's own samples are a moment in it. */}
              {search.trace ? null : (
                <Timeline
                  projectId={projectId}
                  timeWindow={timeWindow}
                  graph={graphQuery}
                  unit={unit}
                />
              )}
              <input
                type="search"
                value={term}
                onChange={(event) => setTerm(event.target.value)}
                placeholder="Search functions"
                aria-label="Search functions"
                className="w-full max-w-sm rounded-sm border border-line bg-surface px-2.5 py-1 font-mono text-ink text-xs outline-none placeholder:text-ink-dim hover:border-line-strong focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1"
              />
            </>
          }
          projectId={projectId}
          timeWindow={timeWindow}
          graph={graphQuery}
          phrase={phrase}
          narrowed={narrowed}
          onClearFilters={clearFilters}
          term={term}
          picked={picked}
          onPick={setPicked}
        />
      )}
    </>
  );
}

/** The type's value over time; dragging across it narrows every panel to that range. */
function Timeline({
  projectId,
  timeWindow,
  graph,
  unit,
}: {
  projectId: string | undefined;
  timeWindow: LiveWindow;
  graph: GraphQuery;
  unit: string;
}) {
  const timeline = useProfileTimeline(projectId, timeWindow, graph);
  if (timeline.isPending) return <SkeletonRows rows={2} label="Loading the timeline" />;
  if (timeline.isError || timeline.data.points.every((point) => point.value === null)) return null;

  const points = timeline.data.points;
  const data = [
    points.map((point) => Date.parse(point.bucket) / 1000),
    points.map((point) => point.value),
  ] as PlotRows;
  const name = typeName(graph.sampleType);
  return (
    <section aria-label={`${name} over time`} className="flex flex-col gap-1">
      <p className="text-ink-dim text-xs">
        {name} over time. Drag across the chart to look at that range.
      </p>
      <TrendChart
        label={name}
        seriesToken={1}
        data={data}
        formatValue={(value) => formatValue(value, timeline.data.unit || unit)}
        description={`${name} per ${timeline.data.bucket_seconds / 60}-minute bucket`}
        // Profiles are sparse — a quiet service is sampled now and then.
        showPoints
        onSelectRange={(from, to) =>
          setAbsoluteWindow(new Date(from * 1000).toISOString(), new Date(to * 1000).toISOString())
        }
      />
    </section>
  );
}

function GraphSection({
  evidence,
  projectId,
  timeWindow,
  graph,
  phrase,
  narrowed,
  onClearFilters,
  term,
  picked,
  onPick,
}: {
  /** The timeline and search: shown under the sentence, the graph's first evidence. */
  evidence: ReactNode;
  projectId: string | undefined;
  timeWindow: LiveWindow;
  graph: GraphQuery;
  phrase: string;
  narrowed: boolean;
  onClearFilters: () => void;
  term: string;
  picked: FrameIdentity | null;
  onPick: (frame: FrameIdentity | null) => void;
}) {
  const result = useFlameGraph(projectId, timeWindow, graph);
  const empty = result.data !== undefined && result.data.samples === 0;
  const latest = useLatestProfile(projectId, graph.service, empty && !narrowed && !graph.spanId);

  if (result.isPending) return <SkeletonRows rows={8} label="Loading the flame graph" />;
  if (result.isError) {
    return (
      <InlineFailure
        title="The flame graph could not be loaded"
        error={result.error}
        onRetry={() => void result.refetch()}
      />
    );
  }
  const data: FlameGraphResponse = result.data;

  if (data.samples === 0) {
    if (graph.spanId) {
      return (
        <EmptyState
          headingLevel={2}
          title="No samples in this span"
          detail="The profiler recorded nothing while this span ran. A span shorter than the sampling interval — about 50 ms — rarely catches a sample."
        />
      );
    }
    if (narrowed) {
      return (
        <EmptyState
          title="No samples match these filters"
          detail={`This service sent ${typePhrase(data.sample_type)} profiles ${phrase}, but none from the host, version or environment chosen.`}
          action={
            <button
              type="button"
              onClick={onClearFilters}
              className="font-medium text-brand-text text-sm hover:underline"
            >
              Clear the filters
            </button>
          }
        />
      );
    }
    const lastReceived = latest.data?.last_received ?? null;
    return (
      <EmptyState
        title="No profiles in this range"
        detail={
          lastReceived
            ? `This service's newest profile is from ${formatTimestamp(lastReceived)}.`
            : `This service sent no ${typePhrase(data.sample_type)} profile ${phrase}.`
        }
        action={
          lastReceived ? (
            <button
              type="button"
              onClick={() => showAround(lastReceived)}
              className="font-medium text-brand-text text-sm hover:underline"
            >
              Show the hour of the newest profile
            </button>
          ) : undefined
        }
      />
    );
  }

  const format = (value: number) => formatValue(value, data.unit);
  const counted = data.unit === "count";
  return (
    <section aria-labelledby="flame-graph" className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        {/* The sentence is the answer; the graph and the table are its evidence (rule 1). */}
        <h2 id="flame-graph" className="font-medium font-serif text-base text-ink">
          {readingOf(
            graph.spanId ? "this span" : graph.service,
            data.sample_type,
            data.total,
            data.functions,
          )}
        </h2>
        <p className="text-ink-dim text-xs">
          {typeName(data.sample_type)} · {format(data.total)}
          {counted
            ? ""
            : ` across ${data.samples.toLocaleString("en")} ${data.samples === 1 ? "sample" : "samples"}`}
        </p>
        {evidence}
        <FlameGraph
          root={data.root}
          formatValue={format}
          mergedFrames={data.merged_frames}
          search={term}
          selected={picked}
        />
      </div>
      <FunctionsTable
        functions={data.functions}
        total={data.total}
        format={format}
        search={term}
        selected={picked}
        onSelect={onPick}
      />
    </section>
  );
}
