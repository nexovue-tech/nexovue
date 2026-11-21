import { Link } from "@tanstack/react-router";

import { useServiceHealthOffer } from "../dashboards-for-service";
import { visibleEscapes } from "../format/visible-escapes";
import { useCan } from "../identity";
import { InlineFailure } from "./InlineFailure";

const ACTION_CLASS =
  "inline-flex min-h-11 items-center self-start text-brand text-sm underline md:min-h-0";

/**
 * "Start a dashboard from this service" (F20, FR41, AC38, AC138): *Service health* as an
 * unsaved draft with the picker on this service, or, once it is saved, "Open Service health
 * for checkout", straight there, so a service's screen never makes a second copy. Starting
 * one is offered only to someone who may save it (`useCan`); opening one is a read.
 */
export function StartFromThisService({
  service,
  projectId,
}: {
  service: string;
  projectId: string;
}) {
  const offer = useServiceHealthOffer();
  const canEdit = useCan("edit_dashboards");

  if (offer.kind === "failed") {
    return (
      <InlineFailure
        title="Could not check for Service health"
        error={offer.error}
        onRetry={offer.retry}
      />
    );
  }
  if (offer.kind === "open") {
    return (
      <Link
        to="/dashboards/$dashboardId"
        params={{ dashboardId: offer.dashboardId }}
        search={{ project: projectId, service }}
        className={ACTION_CLASS}
      >
        Open {visibleEscapes(offer.title)} for {visibleEscapes(service)}
      </Link>
    );
  }
  if (offer.kind === "start" && canEdit) {
    return (
      <Link
        to="/dashboards/new"
        search={{ template: offer.templateId, service }}
        className={ACTION_CLASS}
      >
        Start a dashboard from this service
      </Link>
    );
  }
  return null;
}
