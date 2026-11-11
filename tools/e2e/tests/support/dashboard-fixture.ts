import type { APIRequestContext } from "@playwright/test";

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
