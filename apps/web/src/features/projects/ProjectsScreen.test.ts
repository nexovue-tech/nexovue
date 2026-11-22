// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId } from "@/shared/selection";

import { ProjectsScreen } from "./ProjectsScreen";

/**
 * Settings → Projects, reached by a link to its Deploys section (F28: the releases list's
 * coverage strip, the finding's deploy note). The link names a section inside a project
 * that is closed until someone opens it, so landing on the closed list was a dead end.
 */

const FIRST = { id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100", slug: "staging", name: "Staging" };
const SECOND = {
  id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0200",
  slug: "production",
  name: "Production",
};

const mounted: Root[] = [];
const scrolled: Element[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom lays nothing out and has no scrollIntoView; record who asked.
  Element.prototype.scrollIntoView = function scrollIntoView(this: Element) {
    scrolled.push(this);
  };
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  scrolled.length = 0;
  vi.unstubAllGlobals();
});

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname;
      if (path === "/api/v1/auth/me") return json(identityFor("owner"));
      if (path === "/api/v1/projects") {
        return json({ projects: [FIRST, SECOND].map((p) => ({ ...p, first_reported_on: null })) });
      }
      const detail = [FIRST, SECOND].find((p) => path === `/api/v1/projects/${p.id}`);
      if (detail) {
        return json({
          ...detail,
          retention_days: 30,
          sampling_config: {},
          masking_config: {},
          release_summary_channel_id: null,
          created_at: "2026-09-01T09:00:00Z",
        });
      }
      if (path === "/api/v1/deploys/webhook-secrets") {
        return json({
          project_id: SECOND.id,
          webhook_path: `/api/v1/deploys/webhook/${SECOND.id}`,
          signature_header: "X-Hub-Signature-256",
          secrets: [],
          last_deploy_at: null,
          last_check_at: null,
          last_refusal: null,
          current_versions: [],
          over_release_cap: false,
          mixed_environment_services: [],
        });
      }
      if (path === "/api/v1/findings/stats") {
        return json({
          days: 7,
          opened: 0,
          false_alarms: 0,
          judged_services: 0,
          false_finding_rate_per_service_week: 0,
          services: [],
        });
      }
      // The other sections' lists, all empty: this suite is about the link.
      return json({
        secrets: [],
        variables: [],
        windows: [],
        checks: [],
        channels: [],
        sensitivity: "normal",
        timezone: "UTC",
      });
    }),
  );
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mountAt(entry: string) {
  stubApi();
  setSelectedProjectId(SECOND.id);
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings/projects",
    component: () => createElement(QueryClientProvider, { client }, createElement(ProjectsScreen)),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page]),
    history: createMemoryHistory({ initialEntries: [entry] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  for (let round = 0; round < 10; round += 1) await settle();
  return container;
}

describe("a link to the Deploys section", () => {
  it("opens the selected project and brings its Deploys section into view", async () => {
    const container = await mountAt("/settings/projects#deploys");

    const rows = [...container.querySelectorAll("li > button[aria-expanded]")];
    expect(rows.map((row) => row.getAttribute("aria-expanded"))).toEqual(["false", "true"]);
    const section = container.querySelector("#deploys");
    expect(section).not.toBeNull();
    expect(scrolled).toContain(section);
  });

  it("leaves every project closed when nothing names the section", async () => {
    const container = await mountAt("/settings/projects");

    const rows = [...container.querySelectorAll("li > button[aria-expanded]")];
    expect(rows.map((row) => row.getAttribute("aria-expanded"))).toEqual(["false", "false"]);
    expect(container.querySelector("#deploys")).toBeNull();
  });
});
