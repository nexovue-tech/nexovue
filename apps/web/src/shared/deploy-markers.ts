/**
 * The deploys drawn as vertical rules across a trend chart.
 *
 * This is IA3: the question a chart with a step in it always raises is "did a
 * deploy cause this", and answering it should not mean opening another screen
 * and comparing two clocks by eye.
 *
 * `deploy_events` has been served since the webhook was built and drawn
 * nowhere. Nothing here reads a new table.
 */

/** One rule on the chart, at a moment on its x axis. */
export interface DeployMarker {
  /** Unix **seconds**, which is what uPlot's time scale is in — not millis. */
  at: number;
  version: string;
}

export interface DeployEvent {
  version: string;
  service_name: string;
  deployed_at: string;
}

const MILLISECONDS_PER_SECOND = 1000;

/**
 * The deploys of one service that fall inside the window being charted.
 *
 * A deploy outside it draws nothing rather than being clamped to an edge: a
 * rule pinned to the left of the chart would say "this deploy happened at the
 * start of the window", which is a claim about a time nobody measured.
 *
 * Filtered to the service the chart is about, when one is named. The dashboard
 * names none: there, every deploy in the window belongs on the chart.
 */
export function deployMarkers(
  deploys: readonly DeployEvent[],
  options: { serviceName?: string; from: string; to: string },
): DeployMarker[] {
  const from = Date.parse(options.from);
  const to = Date.parse(options.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return [];

  const markers: DeployMarker[] = [];
  for (const deploy of deploys) {
    // No service named means the whole project — which is what the dashboard
    // asks for, because a deploy that moved rate, errors and latency together
    // is the reading, whichever service shipped it. A service's own chart names
    // one, because a rule from an unrelated service there is an invitation to
    // blame the wrong change.
    if (options.serviceName !== undefined && deploy.service_name !== options.serviceName) continue;
    const at = Date.parse(deploy.deployed_at);
    if (!Number.isFinite(at) || at < from || at > to) continue;
    markers.push({ at: at / MILLISECONDS_PER_SECOND, version: deploy.version });
  }
  return markers.sort((left, right) => left.at - right.at);
}
