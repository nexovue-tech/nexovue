// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import type { RunEvidence, StepEvidence } from "./check-model";
import { EvidenceView } from "./EvidenceView";
import {
  buttonLabelled,
  click,
  failedEvidence,
  readable,
  waitFor,
} from "./render-harness.test-util";

/**
 * The defects these guard: a multistep failure that opens on the wrong request (AC4), an
 * assertion shown without the value it found, and a cut-off body that does not say so
 * (EC13).
 */

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

async function render(evidence: RunEvidence) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(EvidenceView, { evidence })));
  return container;
}

const [FAILED_STEP] = failedEvidence().steps as [StepEvidence];

function step(overrides: Partial<StepEvidence>): StepEvidence {
  return { ...FAILED_STEP, ...overrides };
}

const tabLabels = (container: HTMLElement) =>
  [...container.querySelectorAll('[role="tab"]')].map((tab) => tab.firstElementChild?.textContent);

describe("EvidenceView", () => {
  it("leads with the sentence and opens on the assertions, expected against actual", async () => {
    const container = await render(failedEvidence());

    expect(container.querySelector("p")?.textContent).toBe(
      "The run failed from mumbai on attempt 2: 1 of 2 assertions failed",
    );
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toContain(
      "Assertions",
    );
    const results = [...container.querySelectorAll("li")].map((item) => [
      item.children[0]?.textContent,
      readable(item.children[1] as Element),
    ]);
    expect(results).toEqual([
      ["✓ passed", "status code is less than 400"],
      ["✗ failed", "$.order.id equals 42 — got 41"],
    ]);
  });

  it("says nothing was found rather than showing an empty value", async () => {
    const container = await render(
      failedEvidence({
        steps: [
          step({
            assertions: [
              {
                source: "header",
                property: "X-Request-Id",
                comparison: "is_not_empty",
                target: "",
                actual: null,
                passed: false,
              },
            ],
          }),
        ],
      }),
    );

    expect(readable(container.querySelector("li") as Element)).toContain(
      "X-Request-Id is not empty — got nothing",
    );
  });

  it("opens a multistep run on the request that failed", async () => {
    const container = await render(
      failedEvidence({
        check_type: "multistep",
        steps: [
          step({ position: 0, name: "Log in", passed: true, assertions: [] }),
          step({ position: 1, name: "Create order" }),
          step({ position: 2, name: "Fetch order", passed: true, assertions: [] }),
        ],
      }),
    );

    const requests = [
      ...container.querySelectorAll('ol[aria-label="Requests in this run"] button'),
    ].map((button) => [readable(button), button.getAttribute("aria-current")]);
    expect(requests).toEqual([
      ["1. Log in passed", null],
      ["2. Create order failed", "step"],
      ["3. Fetch order passed", null],
    ]);
    expect(readable(container)).toContain("$.order.id equals 42 — got 41");
  });

  it("shows the response body in the editor, and says when it was cut off", async () => {
    const container = await render(
      failedEvidence({
        steps: [
          step({
            response: {
              status_code: 502,
              headers: [],
              body: {
                text: '{"error": "upstream"}',
                size_bytes: 40_000,
                truncated: true,
                binary: false,
              },
            },
          }),
        ],
      }),
    );

    await click(buttonLabelled(container, "Response"));
    const editor = await waitFor(() => container.querySelector(".cm-content"), "the code editor");

    expect(editor.getAttribute("aria-label")).toBe("Response body");
    expect(editor.textContent).toContain('"error": "upstream"');
    expect(readable(container)).toContain("Status 502");
    expect(readable(container)).toContain(
      "39.1 KiB · cut off: the first 16 KiB is kept as evidence",
    );
  });

  it("names a request that never got an answer, without response tabs", async () => {
    const container = await render(
      failedEvidence({
        check_type: "tcp",
        outcome: "down",
        attempt: 1,
        error: "connection refused",
        steps: [
          step({
            target: "db.example.com:5432",
            request: null,
            response: null,
            assertions: [],
            error: "connection refused",
          }),
        ],
      }),
    );

    expect(container.querySelector("p")?.textContent).toBe(
      "The run failed from mumbai: connection refused",
    );
    expect(tabLabels(container)).toEqual(["Assertions (0)", "Answer", "Timings"]);
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toContain(
      "Answer",
    );
    expect(readable(container.querySelector('[role="tabpanel"]') as Element)).toBe(
      "connection refused",
    );
  });

  it("lists what a DNS lookup answered", async () => {
    const container = await render(
      failedEvidence({
        check_type: "dns",
        outcome: "up",
        attempt: 1,
        error: null,
        steps: [
          step({
            target: "shop.example.com",
            request: null,
            response: null,
            assertions: [],
            answers: ["203.0.113.10", "203.0.113.11"],
            passed: true,
          }),
        ],
      }),
    );

    expect(container.querySelector("p")?.textContent).toBe("The run passed from mumbai.");
    expect(
      [...container.querySelectorAll('[role="tabpanel"] li')].map((item) => item.textContent),
    ).toEqual(["203.0.113.10", "203.0.113.11"]);
  });

  it("marks a retried attempt and a run during maintenance", async () => {
    const container = await render(failedEvidence({ final: false, maintenance: true }));

    expect(readable(container)).toContain(
      "· during maintenance · overruled by a retry, not counted",
    );
  });
});
