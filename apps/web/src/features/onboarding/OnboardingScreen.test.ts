// @vitest-environment jsdom

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
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type { OnboardingViewProps } from "./OnboardingScreen";
import { OnboardingView } from "./OnboardingScreen";

const mounted: Root[] = [];
const clipboard: string[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom has no clipboard at all, and the copy control is the only way to read
  // back what the screen would hand over.
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: (value: string) => {
        clipboard.push(value);
        return Promise.resolve();
      },
    },
  });
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  clipboard.length = 0;
});

const ENDPOINT = "https://ingest.example.com";

const BASE: OnboardingViewProps = {
  setup: { apiKey: "nvk_live_abcdefghijklmnopqrstuv", otlpEndpoint: ENDPOINT, projectId: "p-1" },
  arrived: undefined,
  failed: false,
  silent: false,
};

async function render(overrides: Partial<OnboardingViewProps> = {}): Promise<HTMLElement> {
  const rootRoute = createRootRoute({
    component: () => createElement(OnboardingView, { ...BASE, ...overrides }),
  });
  const children = ["/traces/$traceId", "/synthetics", "/usage"].map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path, component: () => null }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(children),
    history: createMemoryHistory({ initialEntries: ["/onboarding"] }),
  });
  await router.load();

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));
  return container;
}

const readable = async (overrides: Partial<OnboardingViewProps> = {}) =>
  ((await render(overrides)).textContent ?? "").replace(/\s+/g, " ").trim();

describe("the three variables", () => {
  it("hands out the protocol alongside the endpoint and the key", async () => {
    // An SDK whose default is gRPC fails against this HTTP endpoint with an error
    // naming neither problem. Every setup page sets it; so does this screen.
    const text = await readable();

    expect(text).toContain(`OTEL_EXPORTER_OTLP_ENDPOINT=${ENDPOINT}`);
    expect(text).toContain(
      "OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_abcdefghijklmnopqrstuv",
    );
    expect(text).toContain("OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf");
  });

  it("copies all three at once, not just the two it used to", async () => {
    const container = await render();
    const copyAll = container.querySelector<HTMLButtonElement>('[aria-label="Copy all three"]');

    await act(async () => copyAll?.click());

    expect(clipboard).toHaveLength(1);
    expect(clipboard[0]?.split("\n")).toHaveLength(3);
    expect(clipboard[0]).toContain("OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf");
  });

  it("shows a placeholder rather than an empty value when the handoff is gone", async () => {
    // A bare "OTEL_EXPORTER_OTLP_ENDPOINT=" reads as a complete instruction and is
    // copied as one. A missing value has to look missing.
    const text = await readable({ setup: undefined });

    expect(text).toContain("OTEL_EXPORTER_OTLP_ENDPOINT=https://…");
    expect(text).toContain("OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_…");
  });
});

describe("the three states", () => {
  it("waits without offering a diagnosis nothing has arrived to support", async () => {
    const text = await readable();

    expect(text).toContain("Listening for your first span");
    expect(text).toContain("Nothing has arrived yet");
    expect(text).not.toContain("The service has not restarted");
  });

  it("offers the checks once the silence has gone on too long", async () => {
    const text = await readable({ silent: true });

    expect(text).toContain("Still nothing");
    expect(text).toContain("The service has not restarted");
    expect(text).toContain("The endpoint has a path on it");
    expect(text).toContain("The key is missing, or shaped wrong");
    expect(text).toContain("The exporter is speaking gRPC");
    expect(text).toContain("Nothing is getting out");
  });

  it("names none of the checks as the cause", async () => {
    // Nothing has reached us, so there is nothing to diagnose with. The screen
    // must offer the list without claiming to know which item applies.
    const text = await readable({ silent: true });

    expect(text).toContain("We cannot tell you which — nothing has reached us to look at.");
  });

  it("gives the one reachability check, aimed at the real endpoint", async () => {
    const text = await readable({ silent: true });

    expect(text).toContain(`${ENDPOINT}/v1/traces`);
    expect(text).toContain("405 is the answer you want");
  });

  it("keeps trying rather than diagnosing when the check itself failed", async () => {
    // A failed read says nothing about the customer's exporter, so the checklist
    // would be advice about the wrong problem.
    const text = await readable({ silent: true, failed: true });

    expect(text).toContain("We could not check just now");
    expect(text).not.toContain("The service has not restarted");
  });

  it("says what arrived and where it went", async () => {
    const text = await readable({
      arrived: { trace_id: "t-1", root_service: "checkout", root_name: "POST /orders" },
    });

    expect(text).toContain("Your first span arrived.");
    expect(text).toContain("checkout");
    expect(text).toContain("POST /orders");
    expect(text).not.toContain("Listening for your first span");
  });
});

describe("the way out for a service that is not instrumented", () => {
  it("offers the language and platform starting points on the screen itself", async () => {
    const text = await readable();

    expect(text).toContain("My service is not instrumented yet");
    expect(text).toContain("opentelemetry-instrument python app.py");
    expect(text).toContain("Where these variables go, per platform");
    expect(text).toContain("Kubernetes");
  });

  it("names the repository path and invents no documentation URL", async () => {
    // There is no documentation site. A link that does not resolve is worse than
    // a path, which a reader can at least check.
    const text = await readable();

    expect(text).toContain("docs/product/setup/");
    expect(text).not.toMatch(/docs\.nexovue/);
  });
});
