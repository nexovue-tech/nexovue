// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import type { CreatedCheck } from "./api";
import {
  buttonLabelled,
  checkDefinition,
  click,
  describedError,
  inputLabelled,
  json,
  mountAt,
  PROJECT_ID,
  problem,
  readable,
  stubApi,
  submitForm,
  syntheticsReads,
  typeInto,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The defects these guard: a save that sends an unfinished check, a refusal from the API
 * that never reaches the field it names (FR38, AC25), and a heartbeat URL that is lost
 * because the page moved on before anyone could copy it (FR16).
 */

afterEach(unmountAll);

const HEALTH_URL = "https://api.example.com/health";

function created(overrides: Partial<CreatedCheck> = {}): CreatedCheck {
  return { ...checkDefinition({ id: "chk-new" }), heartbeat_token: null, ...overrides };
}

async function mountNew(kind?: string, handlers = {}) {
  const api = stubApi({
    ...syntheticsReads({ checks: [checkDefinition({ id: "chk-new" })] }),
    ...handlers,
  });
  const screen = await mountAt(kind ? `/synthetics/new?kind=${kind}` : "/synthetics/new");
  return { ...screen, api };
}

describe("choosing what to watch", () => {
  it("offers the five kinds, each linking to its form, and the way to import", async () => {
    const { container } = await mountNew();

    const kinds = [...container.querySelectorAll<HTMLAnchorElement>('a[href*="kind="]')].map(
      (link) => [link.getAttribute("href"), link.firstElementChild?.textContent],
    );
    expect(kinds).toEqual([
      ["/synthetics/new?kind=api", "API"],
      ["/synthetics/new?kind=multistep", "Multistep API"],
      ["/synthetics/new?kind=heartbeat", "Heartbeat"],
      ["/synthetics/new?kind=tcp", "TCP"],
      ["/synthetics/new?kind=dns", "DNS"],
    ]);
    expect(container.querySelector('a[href="/synthetics/import"]')?.textContent).toBe("Import it");
    expect(container.querySelector("form")).toBeNull();
  });

  it("opens the form for the kind in the address", async () => {
    const { container } = await mountNew("api");

    expect(container.querySelector("h1")?.textContent).toBe("New API check");
    expect(inputLabelled(container, "URL").value).toBe("");
    expect(buttonLabelled(container, "Create check").type).toBe("submit");
  });
});

describe("creating an API check", () => {
  it("refuses an empty URL beside the field and in the summary, and sends nothing", async () => {
    const { container, api } = await mountNew("api");

    await submitForm(container);

    expect(describedError(inputLabelled(container, "URL"))).toBe("Enter the URL to call.");
    const summary = container.querySelector('[role="alert"]');
    expect(summary?.querySelector("p")?.textContent).toBe(
      "This check cannot run or be saved yet: 1 problem.",
    );
    expect([...(summary?.querySelectorAll("li") ?? [])].map((item) => item.textContent)).toEqual([
      "URL: Enter the URL to call.",
    ]);
    expect(api.to("POST", "/checks")).toEqual([]);
  });

  it("saves a URL with the default status assertion, then opens the check", async () => {
    const { container, router, api } = await mountNew("api", {
      "POST /checks": json(created(), 201),
    });

    await typeInto(inputLabelled(container, "URL"), HEALTH_URL);
    await submitForm(container);
    await waitFor(() => router.state.location.pathname === "/synthetics/chk-new", "the check page");

    const [sent] = api.to("POST", "/checks");
    expect(sent?.body).toEqual({
      project_id: PROJECT_ID,
      name: "GET api.example.com/health",
      config: {
        type: "api",
        request: {
          name: "",
          method: "GET",
          url: HEALTH_URL,
          query: [],
          headers: [],
          body: { kind: "none" },
          auth: { kind: "none" },
          assertions: [
            { source: "status_code", property: "", comparison: "less_than", target: "400" },
          ],
          extractions: [],
          follow_redirects: true,
          timeout_seconds: 10,
        },
      },
      interval_seconds: 60,
      locations: [],
      degraded_ms: null,
      failing_ms: null,
      retries: 1,
    });
    // Leaving a form that was just saved is not leaving unsaved work.
    expect(container.querySelector('[role="alertdialog"]')).toBeNull();
  });

  it("puts the API's reason for refusing the URL under the URL field", async () => {
    const reason = "The address resolves to a private network, which checks may not reach.";
    const { container, router } = await mountNew("api", {
      "POST /checks": problem(422, "The check was refused.", {
        errors: [{ field: "body.config.api.request.url", reason: `Value error, ${reason}` }],
      }),
    });

    await typeInto(inputLabelled(container, "URL"), "https://intranet.example.com/");
    await submitForm(container);
    await waitFor(() => describedError(inputLabelled(container, "URL")), "the URL's error");

    expect(describedError(inputLabelled(container, "URL"))).toBe(reason);
    expect(readable(container.querySelector('[role="alert"]') as Element)).toContain(
      `URL: ${reason}`,
    );
    expect(router.state.location.pathname).toBe("/synthetics/new");
  });
});

describe("creating a heartbeat", () => {
  const TOKEN = "hb_4c1e9a7d2f";

  it("shows the URL once, with a copy control and the warning that it is not shown again", async () => {
    const { container, router, api } = await mountNew("heartbeat", {
      "POST /checks": json(
        created({
          name: "Heartbeat",
          type: "heartbeat",
          config: { type: "heartbeat", grace_seconds: 300 },
          heartbeat_token: TOKEN,
        }),
        201,
      ),
    });

    await submitForm(container);
    await waitFor(() => container.querySelector('[aria-label="Heartbeat created"]'), "the URL");

    const url = `${window.location.origin}/api/v1/heartbeat/${TOKEN}`;
    expect(api.to("POST", "/checks")[0]?.body).toMatchObject({
      name: "Heartbeat",
      config: { type: "heartbeat", grace_seconds: 300 },
      interval_seconds: 3600,
    });
    expect(container.querySelector("code")?.textContent).toBe(url);
    expect(buttonLabelled(container, "Copy heartbeat URL")).toBeDefined();
    expect(readable(container)).toContain("Copy it now — it is not shown again.");
    // Moving on would lose the only copy of the token.
    expect(router.state.location.pathname).toBe("/synthetics/new");
    expect(container.querySelector('a[href="/synthetics/chk-new"]')?.textContent).toBe(
      "Open the check",
    );
  });

  it("can be sent again after a save that failed without a change", async () => {
    const { container, api } = await mountNew("heartbeat", {
      "POST /checks": problem(503, "The service is briefly unavailable."),
    });

    await submitForm(container);
    await waitFor(() => api.to("POST", "/checks").length === 1, "the first save");
    await waitFor(() => readable(container).includes("briefly unavailable"), "the refusal");

    const create = buttonLabelled(container, "Create check");
    expect(create.disabled).toBe(false);
    await click(create);
    await waitFor(() => api.to("POST", "/checks").length === 2, "the second save");
  });
});
