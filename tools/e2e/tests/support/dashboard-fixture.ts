import { type APIRequestContext, expect } from "@playwright/test";

import type { ExportSettings } from "./onboarding";
import { endpointTraffic, exportTrace } from "./otlp";

const MS_PER_MINUTE = 60_000;
/** How long a rollup takes to show a finished minute, measured at 45 s alone and more under load. */
const ROLLUP_ARRIVAL_TIMEOUT_MS = 120_000;

/** A saved dashboard of one chart and one note, for the browser specs of F20's address and phone view. */
export async function saveDashboard(
  request: APIRequestContext,
  options: { title: string; service: string; refreshSeconds?: 30 | 60 | 300 | null },
): Promise<string> {
  const created = await request.post("/api/v1/dashboards", {
    data: {
      title: options.title,
      description: "",
      definition: {
        schema_version: 1,
        refresh_seconds: options.refreshSeconds ?? 60,
        default_range: null,
        service_picker: false,
        links: [{ label: "Runbook", url: "https://wiki.example/runbook" }],
        panels: [
          {
            id: "p_rate",
            kind: "service_trend",
            title: null,
            width: 12,
            height: "large",
            settings: { service: options.service, signal: "rate" },
          },
          {
            id: "p_note",
            kind: "note",
            title: null,
            width: 12,
            height: "small",
            settings: { text: "Check the queue first." },
          },
        ],
      },
    },
  });
  if (!created.ok()) throw new Error(`could not save the dashboard: ${created.status()}`);
  return ((await created.json()) as { id: string }).id;
}

/**
 * Sends one service's requests as one finished minute each, `minutesAgo` back, with the
 * counts given. A chart needs more than one point to move a cursor along, and a minute
 * that is still filling is left off a line (F20 FR66), so history is sent from the past.
 */
export async function exportHistory(
  request: APIRequestContext,
  settings: ExportSettings,
  options: { service: string; endpoint: string; minutes: { minutesAgo: number; count: number }[] },
): Promise<void> {
  for (const { minutesAgo, count } of options.minutes) {
    await exportTrace(request, {
      ...settings,
      batch: endpointTraffic({
        service: options.service,
        endpoint: options.endpoint,
        succeeded: { count, durationMs: 120 },
        failed: { count: 1, durationMs: 300 },
        agoMs: minutesAgo * MS_PER_MINUTE,
      }),
    });
  }
}

/** Waits until the services list holds `service`, which is when a dashboard can draw it. */
export async function waitForService(request: APIRequestContext, service: string): Promise<void> {
  const projects = (await (await request.get("/api/v1/projects")).json()) as {
    projects: { id: string }[];
  };
  const project = projects.projects[0];
  if (!project) throw new Error("the account has no project");
  await expect
    .poll(
      async () => {
        const to = new Date();
        const from = new Date(to.getTime() - 60 * MS_PER_MINUTE);
        const listed = await request.get("/api/v1/services", {
          params: { project_id: project.id, from: from.toISOString(), to: to.toISOString() },
        });
        if (!listed.ok()) return [];
        const body = (await listed.json()) as { services: { service_name: string }[] };
        return body.services.map((each) => each.service_name);
      },
      { timeout: ROLLUP_ARRIVAL_TIMEOUT_MS, intervals: [2_000] },
    )
    .toContain(service);
}
