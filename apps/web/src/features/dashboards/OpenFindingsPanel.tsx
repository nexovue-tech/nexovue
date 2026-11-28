import { detectionHeadlineFor } from "@/shared/detection/headline";
import { UNKNOWN } from "@/shared/format/number";

import type { DashboardContext } from "./api";

const NOT_ALL_JUDGED = "Detection is not judging every service yet.";

/**
 * How many findings are open, from the panel context, with no scan, so it draws through a
 * ClickHouse outage (F20, FR6, T5.6a). While detection is not judging every service, the
 * count is not news: the panel shows a dash and detection's own reason, never 0 (FR63,
 * AC97). A real 0 while judging is shown as one.
 */
export function openFindingsWords(
  context: DashboardContext | undefined,
  service: string | undefined,
): { value: string; reason?: string } {
  if (!context) return { value: UNKNOWN };
  const status = context.detection;
  const allJudged =
    status.state === "judging" && status.services_learning === 0 && status.services_delayed === 0;
  if (!allJudged) {
    const detection = detectionHeadlineFor(status, { zone: context.project.timezone });
    return { value: UNKNOWN, reason: detection?.title ?? NOT_ALL_JUDGED };
  }
  const count =
    service === undefined
      ? context.open_findings.total
      : (context.open_findings.by_service[service] ?? 0);
  return { value: String(count) };
}

export function OpenFindingsPanel({
  context,
  service,
}: {
  context: DashboardContext | undefined;
  service: string | undefined;
}) {
  const words = openFindingsWords(context, service);
  return (
    <div className="flex flex-col gap-1">
      <p className="font-mono text-2xl text-ink">{words.value}</p>
      {words.reason ? <p className="text-ink-dim text-xs">{words.reason}</p> : null}
    </div>
  );
}
