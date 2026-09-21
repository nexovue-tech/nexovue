import { Link } from "@tanstack/react-router";

import { EmptyState } from "@/shared/components/EmptyState";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { CHART_HEIGHT, TrendChart } from "@/shared/components/TrendChart";
import type { DeployMarker } from "@/shared/deploy-markers";
import { deployMarkers } from "@/shared/deploy-markers";
import { formatDuration } from "@/shared/format/duration";
import { formatErrors } from "@/shared/format/health";
import { formatCount, UNKNOWN } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";
import { useLiveWindow, useSelection, useWindowPhrase } from "@/shared/selection";

import type { OverviewService, ProjectTotals } from "./api";
import {
  useChecksSummary,
  useNodesSummary,
  useProjectDeploys,
  useProjectOverview,
  useVitalsSummary,
  useWorstServices,
} from "./api";
import type { OverviewPlot } from "./overview-plots";
import { buildOverviewPlots } from "./overview-plots";

/**
 * How the whole system is running, in one screen.
 *
 * Three panels, then the worst services, then one line per surface. The order
 * is the point: a dashboard that tries to show everything is the failure mode
 * the research names — information overload is the commonest dashboard defect —
 * so the surfaces get a line each and a link, not a panel each.
 *
 * **Nothing here is composed from another screen's read.** The project's p95 is
 * a merge of every service's quantile state, and averaging the service list's
 * finished p95s answers a different number by a factor of a hundred and fifty
 * on real data. `/services/overview` exists for that reason alone.
 */
export function OverviewScreen() {
  const { projectId } = useSelection();
  const timeWindow = useLiveWindow();
  const windowPhrase = useWindowPhrase();

  const overview = useProjectOverview(projectId, timeWindow);
  const worst = useWorstServices(projectId, timeWindow);
  const deploys = useProjectDeploys(projectId);
  // The strip waits for the panels. Five telemetry reads at once is one more
  // than the tier allows in flight, and the fifth is refused rather than
  // queued — so firing them together made one read of this screen fail every
  // time it was opened.
  const panelsLanded = overview.data !== undefined;
  const nodes = useNodesSummary(projectId, timeWindow, panelsLanded);
  const checks = useChecksSummary(projectId, timeWindow, panelsLanded);
  const vitals = useVitalsSummary(projectId, timeWindow, panelsLanded);

  // Every deploy in the window, whichever service shipped it: a deploy that
  // moved rate, errors and latency together is the reading this screen exists
  // to make legible, and on a project-wide chart no one service owns it.
  const markers = deployMarkers(deploys.data ?? [], {
    from: timeWindow.from,
    to: timeWindow.to,
  });

  return (
    <OverviewView
      windowLabel={windowPhrase}
      totals={overview.data?.totals}
      plots={buildOverviewPlots(overview.data?.points ?? [])}
      markers={markers}
      worstServices={worst.data ?? []}
      isPending={overview.isPending}
      isError={overview.isError}
      error={overview.error}
      onRetry={() => void overview.refetch()}
      surfaces={{
        nodes: nodes.data
          ? {
              total: nodes.data.nodes.length,
              worst: nodes.data.nodes.filter((node) => node.stale).length,
            }
          : undefined,
        checks: checks.data
          ? {
              total: checks.data.checks.length,
              worst: checks.data.checks.filter((check) => check.up === false).length,
            }
          : undefined,
        lcp: vitals.data?.vitals.find((vital) => vital.name === "lcp"),
      }}
    />
  );
}

interface SurfaceCounts {
  nodes?: { total: number; worst: number };
  checks?: { total: number; worst: number };
  lcp?: { p75: number; readings: number };
}

export interface OverviewViewProps {
  windowLabel: string;
  totals?: ProjectTotals;
  plots: readonly OverviewPlot[];
  markers: readonly DeployMarker[];
  worstServices: readonly OverviewService[];
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  surfaces: SurfaceCounts;
}

/** Everything the screen shows, with no query client and no clock behind it. */
export function OverviewView({
  windowLabel,
  totals,
  plots,
  markers,
  worstServices,
  isPending,
  isError,
  error,
  onRetry,
  surfaces,
}: OverviewViewProps) {
  // `service_count` is the honest zero: nothing reported, rather than something
  // reporting nothing. A screen of zeroes reads as an outage.
  const reporting = (totals?.service_count ?? 0) > 0;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="shrink-0 px-4 pt-5 pb-3">
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">Overview</h1>
        <p className="mt-1 text-ink-dim text-sm">
          Every service you send traces from, over {windowLabel}.
        </p>
      </header>

      {isPending ? (
        <div className="px-4">
          <SkeletonRows rows={6} label="Loading the overview" />
        </div>
      ) : isError ? (
        <div className="px-4">
          <InlineFailure title="The overview could not be loaded" error={error} onRetry={onRetry} />
        </div>
      ) : !reporting ? (
        <EmptyState
          title="Nothing has reported yet"
          detail="No service has sent a trace to this project in this window. Point an OpenTelemetry SDK at your endpoint and the first one appears here within about a minute — or widen the time range if you expected something older."
        />
      ) : (
        <>
          <Totals totals={totals} />

          <div className="grid gap-4 px-4 pb-4 lg:grid-cols-3">
            {plots.map((plot) => (
              <Panel key={plot.metric} plot={plot} markers={markers} windowLabel={windowLabel} />
            ))}
          </div>

          {markers.length > 0 ? (
            <p className="shrink-0 px-4 pb-3 text-ink-dim text-xs">
              {/* One sentence for all three panels: the rule is the same instant
                  on each, and saying it once is what makes that legible. */}
              Dashed rules are the {formatCount(markers.length)}{" "}
              {markers.length === 1 ? "deploy" : "deploys"} in this window.
            </p>
          ) : null}

          <WorstServices services={worstServices} />
          <SurfaceStrip surfaces={surfaces} />
        </>
      )}
    </div>
  );
}

/** The four numbers a person reads before anything else. */
function Totals({ totals }: { totals?: ProjectTotals }) {
  if (!totals) return null;

  return (
    <dl className="grid shrink-0 grid-cols-2 gap-px border-line border-y bg-line sm:grid-cols-4">
      <Total term="Services" value={formatCount(totals.service_count)} />
      <Total term="Requests/min" value={formatRatePerMinute(totals.requests_per_minute)} />
      <Total
        term="Errors"
        value={formatErrors(totals.error_count, totals.error_rate)}
        critical={totals.error_count > 0}
      />
      <Total term="p95" value={formatDuration(totals.p95_ms)} />
    </dl>
  );
}

function Total({
  term,
  value,
  critical = false,
}: {
  term: string;
  value: string;
  critical?: boolean;
}) {
  return (
    <div className="bg-page px-4 py-3">
      <dt className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{term}</dt>
      <dd className={`mt-0.5 font-mono text-base ${critical ? "text-critical-text" : "text-ink"}`}>
        {value}
      </dd>
    </div>
  );
}

function Panel({
  plot,
  markers,
  windowLabel,
}: {
  plot: OverviewPlot;
  markers: readonly DeployMarker[];
  windowLabel: string;
}) {
  return (
    <section className="rounded-sm border border-line p-3">
      <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{plot.label}</h2>
      {plot.flatAtZero ? (
        // Held at the chart's own height so the row of three stays level.
        <p className="flex items-center text-ink-dim text-sm" style={{ height: CHART_HEIGHT }}>
          {plot.zeroText}
        </p>
      ) : (
        <TrendChart
          label={plot.lines.map((line) => line.label)}
          seriesToken={plot.seriesToken}
          data={[plot.lines[0].data[0], ...plot.lines.map((line) => line.data[1])]}
          markers={markers}
          formatValue={AXIS_FORMAT[plot.metric]}
          description={`${plot.label} across ${windowLabel}.`}
        />
      )}
    </section>
  );
}

/** Each panel's own unit. A raw number on the axis says nothing. */
const AXIS_FORMAT: Record<OverviewPlot["metric"], (value: number) => string> = {
  rate: (value) => formatRatePerMinute(value),
  errorRate: (value) => `${value.toFixed(1)}%`,
  latency: (value) => formatDuration(value),
};

/**
 * The worst services, in the ranking the API already applied.
 *
 * The same visual language as the services screen — severity mark, the same
 * columns — without importing it: a feature never imports another feature, and
 * what is shared between the two is the token layer rather than a row.
 *
 * **No sparkline here, deliberately.** A trend needs its own read, and this
 * screen already makes six. Drawing one from the single p95 the row carries
 * would have rendered an em dash on every row for ever — `Sparkline` refuses
 * fewer than four points, correctly, because one point is not a trend. A
 * column that is always empty is worse than no column: it reads as data we
 * failed to fetch.
 */
function WorstServices({ services }: { services: readonly OverviewService[] }) {
  if (services.length === 0) return null;

  return (
    <section className="shrink-0 border-line border-t">
      <div className="flex items-baseline justify-between px-4 py-2">
        <h2 className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Worst services</h2>
        <Link to="/services" className="text-brand-text text-sm underline-offset-2 hover:underline">
          All services
        </Link>
      </div>

      {/* Three unlabelled numbers is what this was: the KPI band above names them,
          but not on the same line as the rows
          (`docs/quality/console-review-2026-09-20.md`). The widths match the row
          below exactly, so a heading cannot drift off its column. */}
      <div className="flex items-center gap-3 border-line-subtle border-b px-4 pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps">
        <span className="w-2 shrink-0" />
        <span className="min-w-0 flex-1">Service</span>
        <span className="w-20 shrink-0 text-right">Req/min</span>
        <span className="w-28 shrink-0 text-right">Errors</span>
        <span className="w-16 shrink-0 text-right">p95</span>
      </div>

      <ul>
        {services.map((row) => (
          <li key={row.service_name}>
            <Link
              to="/services/$name"
              params={{ name: row.service_name }}
              className="flex items-center gap-3 border-line-subtle border-b px-4 py-1.5 text-sm hover:bg-hover"
            >
              <span className="flex w-2 shrink-0 justify-center">
                {row.error_count > 0 ? (
                  <span aria-hidden="true" className="size-1.5 rounded-full bg-critical-solid" />
                ) : null}
              </span>
              <span className="min-w-0 flex-1 truncate text-ink">{row.service_name}</span>
              <span className="w-20 shrink-0 text-right font-mono text-2xs text-ink-dim">
                {formatRatePerMinute(row.requests_per_minute)}
              </span>
              <span
                className={`w-28 shrink-0 text-right font-mono text-2xs ${
                  row.error_count > 0 ? "text-critical-text" : "text-ink-dim"
                }`}
              >
                {formatErrors(row.error_count, row.error_rate)}
              </span>
              <span className="w-16 shrink-0 text-right font-mono text-2xs text-ink">
                {formatDuration(row.p95_ms)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * One line per surface, not a panel each.
 *
 * A surface that has sent nothing shows an em dash rather than a zero: "0
 * nodes" and "no collector configured" are different findings, and only one of
 * them is a problem with the fleet.
 */
function SurfaceStrip({ surfaces }: { surfaces: SurfaceCounts }) {
  return (
    <section className="grid shrink-0 grid-cols-1 gap-px border-line border-t bg-line sm:grid-cols-3">
      <SurfaceLine
        title="Infrastructure"
        to="/infrastructure"
        summary={
          // Zero is not a reading here. "0 nodes" claims a fleet of none; the
          // truth is that nothing has scraped a machine yet, and the em dash is
          // how every other screen says exactly that.
          surfaces.nodes && surfaces.nodes.total > 0
            ? `${formatCount(surfaces.nodes.total)} ${surfaces.nodes.total === 1 ? "node" : "nodes"}`
            : undefined
        }
        worst={
          surfaces.nodes && surfaces.nodes.worst > 0
            ? `${formatCount(surfaces.nodes.worst)} stale`
            : undefined
        }
      />
      <SurfaceLine
        title="Synthetics"
        to="/synthetics"
        summary={
          surfaces.checks && surfaces.checks.total > 0
            ? `${formatCount(surfaces.checks.total)} ${surfaces.checks.total === 1 ? "check" : "checks"}`
            : undefined
        }
        worst={
          surfaces.checks && surfaces.checks.worst > 0
            ? `${formatCount(surfaces.checks.worst)} down`
            : undefined
        }
      />
      <SurfaceLine
        title="Browser"
        to="/rum"
        summary={
          surfaces.lcp && surfaces.lcp.readings > 0
            ? `LCP ${formatDuration(surfaces.lcp.p75)}`
            : undefined
        }
      />
    </section>
  );
}

function SurfaceLine({
  title,
  to,
  summary,
  worst,
}: {
  title: string;
  to: string;
  summary?: string;
  worst?: string;
}) {
  return (
    <Link to={to} className="flex items-baseline gap-3 bg-page px-4 py-3 hover:bg-hover">
      <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{title}</span>
      <span className="font-mono text-ink text-sm">{summary ?? UNKNOWN}</span>
      {worst ? <span className="font-mono text-2xs text-critical-text">{worst}</span> : null}
    </Link>
  );
}
