// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { CheckInput } from "./api";
import { CheckForm } from "./CheckForm";
import { emptyDraft } from "./check-draft";
import type { CheckType } from "./check-model";
import { LIMITS } from "./check-model";
import {
  buttonLabelled,
  choose,
  click,
  describedError,
  failedEvidence,
  type Handlers,
  inputLabelled,
  json,
  mountAt,
  PROJECT_ID,
  problem,
  readable,
  settle,
  stubApi,
  syntheticsReads,
  typeInto,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The defects these guard: Run now saving what it only meant to try (AC24), a failing
 * assertion shown without what was expected and what came back (US18.15), an edited
 * form left without a word (principle 3), and multistep requests sent in an order other
 * than the one on screen (FR10).
 */

afterEach(unmountAll);

async function mountForm(kind: CheckType, extra: Handlers = {}) {
  const saves: CheckInput[] = [];
  const api = stubApi({
    ...syntheticsReads(),
    "POST /check-runs": json({ request_id: "rq-1", location: "mumbai" }, 202),
    "GET /check-runs/rq-1": json({
      request_id: "rq-1",
      status: "complete",
      evidence: failedEvidence({ run_id: "rq-1", check_id: null }),
    }),
    ...extra,
  });
  const Form = () =>
    createElement(CheckForm, {
      projectId: PROJECT_ID,
      initial: emptyDraft(kind),
      submitLabel: "Create check",
      pendingLabel: "Creating",
      save: {
        mutate: (input) => saves.push(input),
        isPending: false,
        error: null,
        reset: () => {},
      },
      onSaved: () => {},
    });
  const screen = await mountAt("/harness", Form);
  return { ...screen, api, saves };
}

describe("Run now", () => {
  it("runs the unsaved definition and shows the failing assertion's expected and actual", async () => {
    const { container, api, saves } = await mountForm("api");
    await typeInto(inputLabelled(container, "URL"), "https://api.example.com/orders");

    await click(buttonLabelled(container, "Run now"));
    const result = await waitFor(
      () => container.querySelector('[aria-label="Run now result"]'),
      "the run's result",
    );

    expect(api.to("POST", "/check-runs")[0]?.body).toMatchObject({
      project_id: PROJECT_ID,
      name: "GET api.example.com/orders",
      config: { type: "api", request: { method: "GET", url: "https://api.example.com/orders" } },
      degraded_ms: null,
      failing_ms: null,
    });
    expect(api.to("GET", "/check-runs/rq-1")[0]?.search.get("wait_seconds")).toBe("20");
    expect(readable(result)).toContain("Run now — nothing was recorded");
    expect(readable(result)).toContain(
      "The run failed from mumbai on attempt 2: 1 of 2 assertions failed",
    );
    const failed = [...result.querySelectorAll("li")].find((item) =>
      item.textContent?.includes("✗ failed"),
    );
    expect(failed?.children[0]?.textContent).toBe("✗ failed");
    expect(readable(failed?.children[1] as Element)).toBe("$.order.id equals 42 — got 41");
    expect(saves).toEqual([]);
    expect(api.to("POST", "/checks")).toEqual([]);
  });
});

const openTab = async (container: HTMLElement, name: string) => {
  const tab = [...container.querySelectorAll('[role="tab"]')].find((candidate) =>
    candidate.textContent?.startsWith(name),
  );
  if (!tab) throw new Error(`no tab named ${name}`);
  await click(tab);
};

describe("Run now on a draft with problems", () => {
  it("names the problems beside their fields and sends nothing", async () => {
    const { container, api } = await mountForm("api");

    await click(buttonLabelled(container, "Run now"));

    expect(describedError(inputLabelled(container, "URL"))).toBe("Enter the URL to call.");
    expect(readable(container.querySelector('[role="alert"]') as Element)).toContain(
      "URL: Enter the URL to call.",
    );
    expect(api.to("POST", "/check-runs")).toEqual([]);
  });

  it("puts the API's refusal on the rows it names, and drops it at the next edit", async () => {
    const { container } = await mountForm("api", {
      "POST /check-runs": problem(422, "The check was refused.", {
        errors: [
          {
            field: "body.config.api.request.headers.0.value",
            reason: "Value error, must not contain line breaks",
          },
          { field: "body.config.api.request.assertions.0", reason: "Value error, not comparable" },
        ],
      }),
    });
    await typeInto(inputLabelled(container, "URL"), "https://api.example.com/orders");
    await openTab(container, "Headers");
    await click(buttonLabelled(container, "Add header"));
    await click(buttonLabelled(container, "Add header"));
    // Row 1 stays blank and is not sent, so the API's header 0 is row 2 on screen.
    await typeInto(inputLabelled(container, "Headers row 2 name"), "X-Trace");
    await typeInto(inputLabelled(container, "Headers row 2 value"), "abc");

    await click(buttonLabelled(container, "Run now"));
    await waitFor(
      () => describedError(inputLabelled(container, "Headers row 2 value")),
      "the header's error",
    );

    expect(describedError(inputLabelled(container, "Headers row 2 value"))).toBe(
      "must not contain line breaks",
    );
    expect(describedError(inputLabelled(container, "Headers row 1 value"))).toBeUndefined();
    await openTab(container, "Assertions");
    expect(
      readable(container.querySelector('ul[aria-label="Assertions"] li') as Element),
    ).toContain("not comparable");

    await typeInto(inputLabelled(container, "URL"), "https://api.example.com/v2/orders");
    await settle();

    expect(readable(container)).not.toContain("not comparable");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});

describe("a binary body", () => {
  it("is sent as octet-stream and refuses a file over the limit before uploading it", async () => {
    const { container, api } = await mountForm("api");
    await openTab(container, "Body");
    await click(container.querySelector('input[type="radio"][value="binary"]') as Element);

    const contentType = container.querySelector<HTMLInputElement>(
      'input[placeholder="application/octet-stream"]',
    );
    expect(contentType?.value).toBe("application/octet-stream");

    const input = inputLabelled(container, "File to send");
    const oversized = new File([new Uint8Array(LIMITS.maxFileBytes + 1)], "dump.bin");
    Object.defineProperty(input, "files", { value: [oversized], configurable: true });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    await settle();

    expect(readable(container)).toContain("That file is over 3.0 MiB.");
    expect(api.to("POST", "/check-files")).toEqual([]);
  });
});

describe("a multipart body already sending its last allowed file", () => {
  it("refuses another before uploading it, rather than at save (AC6)", async () => {
    let stored = 0;
    const { container, api } = await mountForm("api", {
      "POST /check-files": () => {
        stored += 1;
        return new Response(
          JSON.stringify({ id: `file-${stored}`, name: `part${stored}.txt`, size_bytes: 8 }),
          { status: 201, headers: { "content-type": "application/json" } },
        );
      },
    });
    await openTab(container, "Body");
    await click(container.querySelector('input[type="radio"][value="multipart"]') as Element);

    for (let part = 1; part <= LIMITS.maxFilesPerRequest + 1; part += 1) {
      await click(buttonLabelled(container, "Add part"));
      await choose(
        container.querySelector<HTMLSelectElement>(
          `select[aria-label="Part ${part} kind"]`,
        ) as HTMLSelectElement,
        "file",
      );
      await typeInto(inputLabelled(container, `Part ${part} name`), `file${part}`);
      const input = inputLabelled(container, `Part ${part} file`);
      const file = new File([new Uint8Array(8)], `part${part}.txt`);
      Object.defineProperty(input, "files", { value: [file], configurable: true });
      await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
      await settle();
    }

    expect(readable(container)).toContain("This request already sends 3 files, the most it may.");
    // Three uploads, not four: the refused one never left the browser.
    expect(api.to("POST", "/check-files")).toHaveLength(LIMITS.maxFilesPerRequest);
  });
});

describe("a custom method", () => {
  it("stays a free-text field while a common method is typed, in the case typed", async () => {
    const { container } = await mountForm("api");
    const select = container.querySelector<HTMLSelectElement>('select[id$="-method"]');
    if (!select) throw new Error("no method select");

    await choose(select, "__custom__");
    await typeInto(inputLabelled(container, "Method"), "GE");
    await typeInto(inputLabelled(container, "Method"), "GET");
    expect(inputLabelled(container, "Method").value).toBe("GET");

    await typeInto(inputLabelled(container, "Method"), "propfind");
    expect(inputLabelled(container, "Method").value).toBe("propfind");
    expect(select.value).toBe("__custom__");
  });
});

describe("unsaved changes", () => {
  it("lets an untouched form be left without a word", async () => {
    const { container, router } = await mountForm("api");

    await act(async () => void router.navigate({ to: "/synthetics/import" }));

    expect(container.querySelector('[role="alertdialog"]')).toBeNull();
    expect(router.state.location.pathname).toBe("/synthetics/import");
  });

  it("holds an edited form and says what is at risk", async () => {
    const { container, router } = await mountForm("api");

    await typeInto(inputLabelled(container, "URL"), "https://api.example.com/orders");
    await act(async () => void router.navigate({ to: "/synthetics/import" }));

    const prompt = await waitFor(
      () => container.querySelector('[role="alertdialog"]'),
      "the leave prompt",
    );
    expect(readable(prompt)).toContain("You have changed this check and not saved it.");
    expect(router.state.location.pathname).toBe("/harness");
  });
});

describe("a multistep draft", () => {
  const stepTitles = (container: HTMLElement) =>
    [
      ...container.querySelectorAll<HTMLButtonElement>(
        'ol[aria-label="Requests, in order"] li > button:first-child',
      ),
    ].map((button) => button.children[1]?.textContent);

  const selectedTitle = (container: HTMLElement) =>
    container.querySelector('ol[aria-label="Requests, in order"] [aria-current="step"]')
      ?.children[1]?.textContent;

  async function nameStep(container: HTMLElement, position: number, url: string) {
    await click(
      container.querySelectorAll('ol[aria-label="Requests, in order"] li > button:first-child')[
        position
      ] as Element,
    );
    await typeInto(inputLabelled(container, "URL"), url);
  }

  it("adds, reorders and removes requests, and runs them in the order shown", async () => {
    const { container, api } = await mountForm("multistep");
    expect(stepTitles(container)).toEqual(["GET new request", "GET new request"]);

    await nameStep(container, 0, "https://shop.example.com/login");
    await nameStep(container, 1, "https://shop.example.com/orders");
    await click(buttonLabelled(container, "Add request"));
    expect(selectedTitle(container)).toBe("GET new request");
    await typeInto(inputLabelled(container, "URL"), "https://shop.example.com/receipt");
    expect(stepTitles(container)).toEqual([
      "GET shop.example.com/login",
      "GET shop.example.com/orders",
      "GET shop.example.com/receipt",
    ]);

    await click(buttonLabelled(container, "Move request 3 up"));
    expect(stepTitles(container)).toEqual([
      "GET shop.example.com/login",
      "GET shop.example.com/receipt",
      "GET shop.example.com/orders",
    ]);
    // The moved request stays the one being edited.
    expect(selectedTitle(container)).toBe("GET shop.example.com/receipt");
    expect(inputLabelled(container, "URL").value).toBe("https://shop.example.com/receipt");

    await click(buttonLabelled(container, "Remove request 1"));
    expect(stepTitles(container)).toEqual([
      "GET shop.example.com/receipt",
      "GET shop.example.com/orders",
    ]);
    expect(buttonLabelled(container, "Remove request 1").disabled).toBe(false);

    await click(buttonLabelled(container, "Run all requests now"));
    await waitFor(() => api.to("POST", "/check-runs").length === 1, "the run");
    const sent = api.to("POST", "/check-runs")[0]?.body as {
      config: { steps: Array<{ url: string }> };
    };
    expect(sent.config.steps.map((step) => step.url)).toEqual([
      "https://shop.example.com/receipt",
      "https://shop.example.com/orders",
    ]);
  });

  it("keeps at least one request", async () => {
    const { container } = await mountForm("multistep");

    await click(buttonLabelled(container, "Remove request 2"));

    expect(stepTitles(container)).toEqual(["GET new request"]);
    expect(buttonLabelled(container, "Remove request 1").disabled).toBe(true);
  });
});
