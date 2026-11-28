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
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";

import type { ProjectDetail } from "./api";
import { DeploysSection } from "./DeploysSection";
import type { DeploySettings, MintedDeploySettings } from "./deploys-api";
import { recipe, refusalSentence } from "./deploys-api";
import { ProjectSettings } from "./ProjectSettings";

/**
 * Settings → Projects, *Deploys* (F28 FR1, FR2, AC2, AC4). Above all: a minted secret
 * is on the page once and never again, and nobody without `manage_credentials` sees
 * the section at all.
 */

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100";
const SETTINGS_PATH = "/api/v1/deploys/webhook-secrets";
const MINTED_VALUE = "nvdw_marker-7f3c9a1e-shown-once";
const PROJECT: ProjectDetail = {
  id: PROJECT_ID,
  slug: "production",
  name: "Production",
  retention_days: 30,
  sampling_config: {},
  masking_config: {},
  release_summary_channel_id: null,
  created_at: "2026-09-01T09:00:00Z",
};
const PROJECT_PATH = `/api/v1/projects/${PROJECT_ID}`;
const SUMMARY_PATH = `${PROJECT_PATH}/release-summary`;
// The organization's channels: two people read, two machines read.
const CHANNELS = [
  { id: "owners-1", kind: "owners", name: "Organization owners" },
  { id: "slack-1", kind: "slack", name: "#releases" },
  { id: "pager-1", kind: "pagerduty", name: "On-call pager" },
  { id: "hook-1", kind: "webhook", name: "Deploy bot" },
];

function settings(overrides: Partial<DeploySettings> = {}): DeploySettings {
  return {
    project_id: PROJECT_ID,
    webhook_path: `/api/v1/deploys/webhook/${PROJECT_ID}`,
    signature_header: "X-Hub-Signature-256",
    secrets: [],
    last_deploy_at: null,
    last_check_at: null,
    last_refusal: null,
    current_versions: [],
    over_release_cap: false,
    mixed_environment_services: [],
    ...overrides,
  };
}

const SECRET = {
  id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0200",
  hint: "wxyz",
  created_at: "2026-11-01T09:00:00Z",
  last_used_at: null,
};

// The detection section's reading, empty: this suite is about another section.
const NO_FINDINGS = {
  days: 7,
  opened: 0,
  false_alarms: 0,
  judged_services: 0,
  false_finding_rate_per_service_week: 0,
  services: [],
};

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

function stubApi({
  role = "owner",
  current = settings(),
  summaryChannel = null,
}: {
  role?: TestRole;
  current?: DeploySettings;
  summaryChannel?: string | null;
} = {}) {
  let listed = current;
  let project: ProjectDetail = { ...PROJECT, release_summary_channel_id: summaryChannel };
  const calls: string[] = [];
  const saved: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push(`${method} ${url.pathname}`);
      if (url.pathname === "/api/v1/auth/me") return json(identityFor(role));
      if (url.pathname === SETTINGS_PATH && method === "POST") {
        const minted = { ...SECRET, id: "minted", hint: "once", secret: MINTED_VALUE };
        listed = { ...listed, secrets: [...listed.secrets, minted] };
        const { secret: _value, ...shownAfter } = minted;
        listed = { ...listed, secrets: [...current.secrets, shownAfter] };
        const answer: MintedDeploySettings = { ...listed, minted };
        return json(answer, 201);
      }
      if (url.pathname === SETTINGS_PATH) return json(listed);
      if (url.pathname === SUMMARY_PATH && method === "PUT") {
        const body = JSON.parse(String(init?.body)) as { channel_id: string | null };
        saved.push(body);
        project = { ...project, release_summary_channel_id: body.channel_id };
        return json({ project_id: PROJECT_ID, channel_id: body.channel_id });
      }
      if (url.pathname === PROJECT_PATH) return json(project);
      if (url.pathname === "/api/v1/channels") return json({ channels: CHANNELS });
      if (url.pathname === "/api/v1/findings/stats") return json(NO_FINDINGS);
      // The other sections' lists, all empty: this suite is about one section.
      if (method === "GET") {
        return json({
          secrets: [],
          variables: [],
          windows: [],
          checks: [],
          sensitivity: "normal",
          timezone: "UTC",
        });
      }
      throw new Error(`no stub for ${method} ${url.pathname}`);
    }),
  );
  return { calls, saved };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(component: () => ReturnType<typeof createElement>) {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => createElement(QueryClientProvider, { client }, component()),
  });
  const elsewhere = createRoute({
    getParentRoute: () => rootRoute,
    path: "/elsewhere",
    component: () => createElement("p", null, "Somewhere else"),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page, elsewhere]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  for (let round = 0; round < 8; round += 1) await settle();
  routers.set(container, router);
  return container;
}

const routers = new Map<HTMLElement, { navigate: (to: { to: string }) => Promise<void> }>();

const section = () => mount(() => createElement(DeploysSection, { projectId: PROJECT_ID }));

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();

function buttonLabelled(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

async function click(button: HTMLButtonElement) {
  await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  for (let round = 0; round < 4; round += 1) await settle();
}

describe("who sees the section", () => {
  it.each(["member", "viewer"] as const)(
    "is hidden from a %s, and says whose it is",
    async (role) => {
      const { calls } = stubApi({ role });
      const container = await mount(() =>
        createElement(ProjectSettings, { project: PROJECT, onDeleted: () => {} }),
      );

      expect(container.querySelector("#deploys")).toBeNull();
      expect(text(container)).toContain("deploy secrets");
      expect(calls).not.toContain(`GET ${SETTINGS_PATH}`);
    },
  );

  it.each(["owner", "admin"] as const)("is shown to an %s", async (role) => {
    stubApi({ role });
    const container = await mount(() =>
      createElement(ProjectSettings, { project: PROJECT, onDeleted: () => {} }),
    );

    expect(container.querySelector("#deploys")).not.toBeNull();
  });
});

describe("a minted secret", () => {
  it("is shown once, and gone from the page once stored", async () => {
    stubApi();
    const container = await section();

    await click(buttonLabelled(container, "Mint a secret"));
    expect(text(container)).toContain(MINTED_VALUE);
    expect(text(container)).toContain("It will not be shown again");

    await click(buttonLabelled(container, "I have stored it"));
    expect(text(container)).not.toContain(MINTED_VALUE);
    expect(text(container)).toContain("…once");
    for (const input of container.querySelectorAll("input")) {
      expect(input.value).not.toContain(MINTED_VALUE);
    }
  });

  it("warns before leaving while it is on the page", async () => {
    stubApi();
    const container = await section();
    await click(buttonLabelled(container, "Mint a secret"));

    await act(async () => void routers.get(container)?.navigate({ to: "/elsewhere" }));
    await settle();

    const prompt = container.querySelector('[role="alertdialog"]');
    expect(prompt?.textContent).toContain("a deploy secret you have not stored yet");
    expect(text(container)).toContain(MINTED_VALUE);
  });

  it("cannot be minted past the limit, and says why", async () => {
    stubApi({
      current: settings({
        secrets: Array.from({ length: 5 }, (_, n) => ({ ...SECRET, id: `s${n}`, hint: `h${n}` })),
      }),
    });
    const container = await section();

    expect(buttonLabelled(container, "Mint a secret").disabled).toBe(true);
    expect(text(container)).toContain("at most 5 live secrets");
  });
});

describe("what last arrived", () => {
  it("says nothing arrived, and where a wrong signature shows instead", async () => {
    stubApi();
    const container = await section();

    const status = container.querySelector('[role="status"]');
    expect(status && text(status)).toContain(
      "Nothing received yet. A wrong signature is not recorded here: your CI’s output shows a 401.",
    );
  });

  it("names the last refusal's item and field", async () => {
    stubApi({
      current: settings({
        last_check_at: new Date().toISOString(),
        last_refusal: {
          at: new Date(Date.now() - 120_000).toISOString(),
          position: 3,
          field: "url",
          secret_id: SECRET.id,
        },
      }),
    });
    const container = await section();

    const status = container.querySelector('[role="status"]');
    expect(status && text(status)).toMatch(/Item 3, field `url`, 2 min/);
  });

  it("warns when older releases are being removed early", async () => {
    stubApi({ current: settings({ over_release_cap: true }) });
    const container = await section();

    expect(text(container)).toContain("Older releases are being removed early");
  });
});

describe("the recipes", () => {
  const url = `https://nexovue.example.com/api/v1/deploys/webhook/${PROJECT_ID}`;

  it.each(["github", "gitlab", "curl"] as const)(
    "%s reads the secret from the environment, never as an argument",
    (kind) => {
      const text = recipe(kind, url);

      expect(text).toContain(url);
      expect(text).toContain('os.environ["NEXOVUE_DEPLOY_SECRET"]');
      expect(text).not.toMatch(/-hmac|--secret|\$NEXOVUE_DEPLOY_SECRET/);
      expect(text).toContain("exit 0");
      expect(text).not.toContain("metadata");
    },
  );

  it("gives GitHub a delivery id that is new on a re-run", () => {
    // biome-ignore lint/suspicious/noTemplateCurlyInString: GitHub's own expression syntax.
    expect(recipe("github", url)).toContain("${{ github.run_id }}-${{ github.run_attempt }}");
  });

  it("words a whole-body refusal without an item", () => {
    expect(
      refusalSentence(
        { at: new Date().toISOString(), position: null, field: null, secret_id: null },
        Date.now(),
      ),
    ).toMatch(/^The whole body, /);
  });
});

describe("FR45 and FR55 in the section", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-12T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("warns that two environments share the project, from the stored reading", async () => {
    stubApi({ current: settings({ mixed_environment_services: ["checkout", "search"] }) });
    const container = await section();

    expect(text(container)).toContain(
      "Two environments appear to share this project, so these comparisons mix them. Seen on checkout, search.",
    );
  });

  it("says nothing of environments when no reading saw two", async () => {
    stubApi();
    const container = await section();

    expect(text(container)).not.toContain("Two environments");
  });

  it("is off by default and offers only channels people read", async () => {
    stubApi();
    const container = await section();

    const select = container.querySelector<HTMLSelectElement>("#deploys select");
    expect(select?.value).toBe("off");
    expect([...(select?.options ?? [])].map((option) => option.textContent)).toEqual([
      "Off",
      "Organization owners",
      "#releases",
    ]);
    expect(buttonLabelled(container, "Save").disabled).toBe(true);
  });

  it("saves the chosen channel, and null to turn it off", async () => {
    const { saved } = stubApi({ summaryChannel: "owners-1" });
    const container = await section();
    const select = container.querySelector<HTMLSelectElement>("#deploys select");
    expect(select?.value).toBe("owners-1");

    await choose(select, "slack-1");
    await click(buttonLabelled(container, "Save"));
    await choose(container.querySelector<HTMLSelectElement>("#deploys select"), "off");
    await click(buttonLabelled(container, "Save"));

    expect(saved).toEqual([{ channel_id: "slack-1" }, { channel_id: null }]);
  });
});

async function choose(select: HTMLSelectElement | null, value: string) {
  if (!select) throw new Error("no channel chooser");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
    setter?.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
}
