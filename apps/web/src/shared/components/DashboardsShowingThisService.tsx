import { Link } from "@tanstack/react-router";
import type { OpenWindow } from "../dashboards-for-service";
import { useDashboardsForService } from "../dashboards-for-service";
import { visibleEscapes } from "../format/visible-escapes";
import { InlineFailure } from "./InlineFailure";
import { SkeletonText } from "./Skeleton";

/**
 * "Dashboards showing this service" (F20, FR57, AC82, AC117), offered by a finding, a
 * service and an alert incident whose rule names one. Dashboards naming the service come
 * first, then by last opened, at most five, with "See all" when more exist; the order is the
 * API's, from stored columns, so it answers whatever the telemetry store is doing. Each opens
 * with the service picked, in the project the thing belongs to, on `window`.
 */
export function DashboardsShowingThisService({
  service,
  projectId,
  window,
}: {
  service: string;
  projectId: string;
  /** What the link opens on: the finding's own span, or the reader's live window. */
  window: OpenWindow | undefined;
}) {
  const found = useDashboardsForService(service);
  const search = {
    project: projectId,
    service,
    ...(window?.kind === "live" ? { range: window.rangeId } : {}),
    ...(window?.kind === "fixed" ? { from: window.from, to: window.to } : {}),
  };

  return (
    <section aria-labelledby="dashboards-for-service" className="flex flex-col gap-1">
      <h2 id="dashboards-for-service" className="heading-section">
        Dashboards showing this service
      </h2>
      {found.isPending ? (
        <SkeletonText width="half" />
      ) : found.isError ? (
        <InlineFailure
          title="Could not list dashboards"
          error={found.error}
          onRetry={() => void found.refetch()}
        />
      ) : found.data.dashboards.length === 0 ? (
        <p className="text-ink-dim text-sm">No dashboard shows {visibleEscapes(service)} yet.</p>
      ) : (
        <>
          <ul className="flex flex-col gap-1">
            {found.data.dashboards.map((dashboard) => (
              <li key={dashboard.id} className="text-sm">
                <Link
                  to="/dashboards/$dashboardId"
                  params={{ dashboardId: dashboard.id }}
                  search={search}
                  className="text-ink underline-offset-2 hover:underline"
                >
                  {visibleEscapes(dashboard.title)}
                </Link>
              </li>
            ))}
          </ul>
          {found.data.more ? (
            <Link
              to="/dashboards"
              className="text-ink-dim text-sm underline-offset-2 hover:underline"
            >
              See all
            </Link>
          ) : null}
        </>
      )}
    </section>
  );
}
