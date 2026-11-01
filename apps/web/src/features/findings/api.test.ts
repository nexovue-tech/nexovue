// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { TimeWindow } from "@/shared/selection";

import type { DetectionStatus, FindingDetail, FindingsResponse } from "./api";
import { useDetectionStatus, useFindingAction, useFindings } from "./api";

/**
 * The feed's read: what it asks `GET /findings` for, and that a failed read stays a
 * failure rather than arriving as an empty feed, which would read as "nothing is wrong".
 */

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a00aa";
const WINDOW: TimeWindow = { from: "2026-09-13T14:00:00Z", to: "2026-09-13T15:00:00Z" };
const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const RESPONSE: FindingsResponse = {
  findings: [
    {
      id: "f1",
      project_id: PROJECT_ID,
      scope: "service",
      service_name: "checkout-api",
      title: "Checkout is 4× slower than normal since 15:04",
      severity: "critical",
      state: "closed",
      closed_reason: "service_gone",
      muted: false,
      started_at: "2026-09-13T15:04:00Z",
      closed_at: "2026-09-13T16:00:00Z",
      acknowledged_at: null,
      signals: [
        {
          signal: "slower",
          normal: 340,
          current: null,
          worst: 3_900,
          unit: "ms",
          comparison: "4× slower than normal",
          endpoint: null,
        },
      ],
      trend: [1, 2, 3],
    },
  ],
  muted_count: 2,
  truncated: false,
};

function stubFetch(response: () => Response) {
  const fetch = vi.fn(async (_url: URL) => response());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

type Seen = { current?: ReturnType<typeof useFindings> };

function Probe({ muted, seen }: { muted: boolean; seen: Seen }) {
  seen.current = useFindings(PROJECT_ID, muted, WINDOW);
  return null;
}

/** Mounts the hook and hands back whatever it last returned. */
function mount(muted: boolean) {
  const seen: Seen = {};
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  act(() =>
    root.render(
      createElement(QueryClientProvider, { client }, createElement(Probe, { muted, seen })),
    ),
  );
  return seen;
}

describe("useFindings", () => {
  it("asks for every finding, open and closed, leaving muted ones out", async () => {
    const fetch = stubFetch(
      () =>
        new Response(JSON.stringify(RESPONSE), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );

    const seen = mount(false);

    await vi.waitFor(() => expect(seen.current?.isSuccess).toBe(true));
    const url = fetch.mock.calls[0]?.[0];
    expect(url?.pathname).toBe("/api/v1/findings");
    expect(Object.fromEntries(url?.searchParams ?? [])).toEqual({
      project_id: PROJECT_ID,
      state: "all",
      muted: "false",
    });
    // The feed asks by state, not by window: an open finding older than the window
    // still needs somebody.
    expect(url?.searchParams.has("from")).toBe(false);
    expect(seen.current?.data).toEqual(RESPONSE);
  });

  it("asks for the muted findings when the reader opens them", async () => {
    const fetch = stubFetch(
      () =>
        new Response(JSON.stringify({ findings: [], muted_count: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );

    const seen = mount(true);

    await vi.waitFor(() => expect(seen.current?.isSuccess).toBe(true));
    expect(fetch.mock.calls[0]?.[0].searchParams.get("muted")).toBe("true");
  });

  it("stays a failure when the read fails, never an empty feed", async () => {
    // AC18: an empty feed reads as "nothing is wrong", and a failed read must not.
    stubFetch(
      () =>
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Internal Server Error",
            status: 500,
          }),
          { status: 500, headers: { "content-type": "application/problem+json" } },
        ),
    );

    const seen = mount(false);

    await vi.waitFor(() => expect(seen.current?.isError).toBe(true));
    expect(seen.current?.data).toBeUndefined();
  });
});

type SeenStatus = { current?: ReturnType<typeof useDetectionStatus> };

function StatusProbe({ seen }: { seen: SeenStatus }) {
  seen.current = useDetectionStatus(PROJECT_ID, WINDOW);
  return null;
}

function mountStatus() {
  const seen: SeenStatus = {};
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  act(() =>
    root.render(
      createElement(QueryClientProvider, { client }, createElement(StatusProbe, { seen })),
    ),
  );
  return seen;
}

describe("useDetectionStatus", () => {
  it("reads the project's detection status", async () => {
    const STATUS: DetectionStatus = {
      state: "learning",
      services_total: 2,
      services_learning: 2,
      services_judged: 0,
      services_delayed: 0,
      services_muted: 0,
      earliest_judging_date: "2026-11-03",
      not_running_since: null,
      delayed_since: null,
      open_findings: 0,
      services_too_many: 0,
      judged_service_limit: 500,
    };
    const fetch = stubFetch(
      () =>
        new Response(JSON.stringify(STATUS), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );

    const seen = mountStatus();

    await vi.waitFor(() => expect(seen.current?.isSuccess).toBe(true));
    expect(fetch.mock.calls[0]?.[0].pathname).toBe(
      `/api/v1/projects/${PROJECT_ID}/detection/status`,
    );
    expect(seen.current?.data).toEqual(STATUS);
  });

  it("stays a failure when the read fails, so the screen cannot show a healthy line", async () => {
    stubFetch(
      () =>
        new Response(
          JSON.stringify({ type: "about:blank", title: "Internal Server Error", status: 500 }),
          { status: 500, headers: { "content-type": "application/problem+json" } },
        ),
    );

    const seen = mountStatus();

    await vi.waitFor(() => expect(seen.current?.isError).toBe(true));
    expect(seen.current?.data).toBeUndefined();
  });
});

type ActionSeen = { current?: ReturnType<typeof useFindingAction> };

function ActionProbe({ findingId, seen }: { findingId: string; seen: ActionSeen }) {
  seen.current = useFindingAction(findingId);
  return null;
}

describe("useFindingAction", () => {
  it("reads again what a closed finding changes: the feed, the status, the counts and the service's markers", async () => {
    const [found] = RESPONSE.findings;
    if (!found) throw new Error("the fixture has a finding");
    const changed: FindingDetail = { ...found, series: [], incidents: [], services: [] };
    stubFetch(
      () =>
        new Response(JSON.stringify({ finding: changed, resolved_to: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidated = vi.spyOn(client, "invalidateQueries");
    const seen: ActionSeen = {};
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    mounted.push(root);
    act(() =>
      root.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(ActionProbe, { findingId: found.id, seen }),
        ),
      ),
    );

    await act(async () => {
      await seen.current?.mutateAsync({ action: "false-alarm" });
    });

    const keys = invalidated.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toEqual(
      expect.arrayContaining([
        ["findings", "feed"],
        ["findings", "detection"],
        ["detection", "stats", PROJECT_ID],
        ["detection", "service", PROJECT_ID],
      ]),
    );
  });
});
