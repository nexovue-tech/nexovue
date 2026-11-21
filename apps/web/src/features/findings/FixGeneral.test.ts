// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TestRole } from "@/shared/identity.test-util";

import type { FindingGeneralFix, GeneralFix } from "./api";
import { GENERAL_WORDS, SUGGESTED_FIX } from "./fix-page";
import {
  finding,
  json,
  mountFinding,
  problem,
  readable,
  settle,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * F5's rung 2b on the finding page (plan T2b.8; spec FR33, FR37; D70; AC72, AC77's screen
 * half). Only the network is replaced, and the clock is fixed. The label comes first, word
 * for word; the example is text with no copy control; a viewer reads what a member reads,
 * without the offer; older suggestions stay listed.
 */

const NOW = new Date("2026-11-21T12:00:00Z");
const LABEL = "Example pattern, not your code. Nexovue has not seen your code. Check before using.";
const LEAD = "Written from this finding's facts only, in python. Not compiled and not run.";
// Markup in a model's code stays characters, never an element (FR37).
const CODE = "import time\n<b>bold</b>\ntime.sleep(1)\n";

const general = (overrides: Partial<FindingGeneralFix> = {}): FindingGeneralFix => ({
  finding_id: "f-1",
  line: null,
  offer: null,
  latest: null,
  earlier: [],
  ...overrides,
});

const PROPOSED: GeneralFix = {
  id: "x-1",
  state: "proposed",
  heading: "General fix suggestion",
  sentence: null,
  content: {
    heading: "General fix suggestion",
    label: LABEL,
    lead: LEAD,
    summary: [{ text: "Calls from " }, { name: "checkout-api" }, { text: " began failing." }],
    steps: [[{ text: "Wrap the outbound call in a retry." }]],
    language: "python",
    code: CODE,
    evidence: [{ evidence_id: "deploy:d-1", number: 2 }],
    earlier_reading: null,
  },
  tokens_used: 1460,
  verdict: null,
  requested_at: "2026-11-21T11:56:00Z",
  finished_at: "2026-11-21T11:57:00Z",
};

const OFFER = general({
  line: {
    heading: "Suggested fix not available",
    sentence: "Nexovue does not know where this service's code lives.",
  },
  offer: {
    action: "Suggest a general fix",
    lead: "Without access to the code:",
    send_sentence:
      "Sends this finding's facts, and the language you picked, to provider.example. No code is read or sent.",
    languages: [
      "python",
      "javascript",
      "typescript",
      "java",
      "kotlin",
      "go",
      "ruby",
      "php",
      "csharp",
      "rust",
    ],
    preselected: null,
    tries_left: 3,
  },
});

// AC37's scan, with AC72's allow-list and FR1's "Suggested fix".
const BANNED =
  /\b(?:tested|verified|safe|root cause|approved|injection-proof|passed|fix|fixes|fixed)\b|Fix App/i;
const ALLOWED = /Suggest a general fix|General fix suggestion|[Ss]uggested fix(?:es)?/g;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

async function page(
  rung2b: FindingGeneralFix,
  { role = "member" as TestRole, extra = {} as Record<string, ReturnType<typeof json>> } = {},
) {
  const api = stubApi(finding(), {
    role,
    extra: { "GET /findings/f-1/fixes/general": json(rung2b), ...extra },
  });
  const { container } = await mountFinding();
  await waitFor(() => container.querySelector("h1"), "the finding's sentence");
  for (let round = 0; round < 6; round += 1) await settle();
  const section = container.querySelector<HTMLElement>(`#${SUGGESTED_FIX.anchor}`);
  return { api, container, section };
}

describe("a general suggestion (FR37, AC72)", () => {
  it("shows the label first, word for word, then the lead, the words and the example", async () => {
    const { section } = await page(general({ latest: PROPOSED }));

    expect(section).not.toBeNull();
    const text = readable(section as Element);
    expect(section?.querySelector("h3")?.textContent).toBe("General fix suggestion");
    expect(section?.querySelector('[data-testid="general-label"]')?.textContent).toBe(LABEL);
    expect(text.indexOf(LABEL)).toBeLessThan(text.indexOf(LEAD));
    expect(text.indexOf(LEAD)).toBeLessThan(text.indexOf("Calls from checkout-api"));
    expect(text).toContain("Rests on fact 2 above.");
    expect(text).toContain("1,460 tokens");
  });

  it("renders the example as text in a labelled region, with no copy control (D70)", async () => {
    const { section } = await page(general({ latest: PROPOSED }));

    const code = section?.querySelector('[data-testid="general-code"]');
    expect(code?.textContent).toBe(CODE);
    expect(code?.querySelector("b")).toBeNull();
    const region = section?.querySelector('section[aria-label="Example code, Python"]');
    expect(region?.getAttribute("tabindex")).toBe("0");
    expect([...(section?.querySelectorAll("button") ?? [])].map(readable)).not.toContainEqual(
      expect.stringMatching(/copy/i),
    );
  });

  it("AC74: a viewer reads the whole suggestion, without the offer", async () => {
    const { section } = await page(general({ latest: PROPOSED }), { role: "viewer" });

    expect(readable(section as Element)).toContain(LABEL);
    expect(section?.querySelector("select")).toBeNull();
  });

  it("says a suggestion is being written, in the always-present status line", async () => {
    const proposing: GeneralFix = {
      ...PROPOSED,
      state: "proposing",
      heading: "Writing a general suggestion",
      sentence: "Writing a general suggestion. You may leave this page.",
      content: null,
    };
    const { section } = await page(general({ latest: proposing }));

    expect(readable(section as Element)).toContain("You may leave this page.");
    expect(section?.querySelector('[role="status"]')?.textContent).toBe("Being written");
  });

  it("AC77: older suggestions stay listed, quietly, with state and time", async () => {
    const { section } = await page(
      general({
        latest: PROPOSED,
        earlier: [{ id: "x-0", state: "refused", requested_at: "2026-11-21T10:00:00Z" }],
      }),
    );

    const earlier = section?.querySelector(`section[aria-label="${GENERAL_WORDS.earlier}"]`);
    expect(readable(earlier as Element)).toContain("No general suggestion · asked 2 hr. ago");
  });
});

describe("the offer (FR31 to FR33)", () => {
  it("lists FR33's languages unselected, names the provider, and says tries left", async () => {
    const { section } = await page(OFFER);

    const text = readable(section as Element);
    expect(text).toContain("Nexovue does not know where this service's code lives.");
    expect(text).toContain("to provider.example. No code is read or sent.");
    expect(text).toContain("Tries left today: 3");
    const select = section?.querySelector("select");
    expect(select?.value).toBe("");
    expect([...(select?.options ?? [])].map((option) => option.textContent)).toEqual([
      "Choose a language",
      "Python",
      "JavaScript",
      "TypeScript",
      "Java",
      "Kotlin",
      "Go",
      "Ruby",
      "PHP",
      "C#",
      "Rust",
    ]);
  });

  it("asks for a language first, and sends nothing until one is chosen", async () => {
    const { api, section } = await page(OFFER);
    const button = [...(section?.querySelectorAll("button") ?? [])].find(
      (each) => readable(each) === "Suggest a general fix",
    );

    await act(async () => button?.click());

    expect(readable(section as Element)).toContain(GENERAL_WORDS.pickFirst);
    expect(api.to("POST", "/findings/f-1/fixes/general")).toEqual([]);
  });

  it("sends the picked language and nothing else", async () => {
    const { api, section } = await page(OFFER, {
      extra: { "POST /findings/f-1/fixes/general": json(general({ latest: PROPOSED })) },
    });
    const select = section?.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = "go";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const button = [...(section?.querySelectorAll("button") ?? [])].find(
      (each) => readable(each) === "Suggest a general fix",
    );

    await act(async () => button?.click());
    for (let round = 0; round < 4; round += 1) await settle();

    const [sent] = api.to("POST", "/findings/f-1/fixes/general");
    expect(sent?.body).toEqual({ language: "go" });
  });

  it("shows the API's own sentence when the click is refused", async () => {
    const { section } = await page(OFFER, {
      extra: {
        "POST /findings/f-1/fixes/general": problem(409, "A suggested fix is being prepared."),
      },
    });
    const select = section?.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = "python";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const button = [...(section?.querySelectorAll("button") ?? [])].find(
      (each) => readable(each) === "Suggest a general fix",
    );

    await act(async () => button?.click());
    for (let round = 0; round < 4; round += 1) await settle();

    expect(readable(section as Element)).toContain("A suggested fix is being prepared.");
  });
});

describe("AC37 over every rung 2b state (AC72)", () => {
  it.each<[string, FindingGeneralFix]>([
    ["offered", OFFER],
    ["proposed", general({ latest: PROPOSED })],
    [
      "refused",
      general({
        latest: {
          ...PROPOSED,
          state: "refused",
          heading: "No general suggestion",
          sentence:
            "The general suggestion was withheld because it held a web address or host name. Asking again may help.",
          content: null,
        },
      }),
    ],
  ])("the %s state says no banned word", async (_, rung2b) => {
    const { section } = await page(rung2b);

    expect(readable(section as Element).replace(ALLOWED, "")).not.toMatch(BANNED);
  });
});

describe("Step 5: was this general suggestion useful? (FR29, decision 25)", () => {
  it("asks under the suggestion, and the answer posts to the same verdict route", async () => {
    const asked: GeneralFix = {
      ...PROPOSED,
      verdict: {
        question: "Was this suggestion useful?",
        answers: [
          { value: "merge_as_is", label: "Yes, nearly as it is" },
          { value: "useful_start", label: "Yes, as a start" },
          { value: "wrong", label: "No, it was wrong" },
        ],
        chosen: null,
        recorded: null,
      },
    };
    const { api, section } = await page(general({ latest: asked }), {
      extra: {
        "POST /findings/f-1/fixes/x-1/verdict": json({
          ...asked.verdict,
          chosen: "wrong",
          recorded: "Recorded. This changes nothing else.",
        }),
      },
    });

    const text = readable(section as Element);
    expect(text.indexOf(LABEL)).toBeLessThan(text.indexOf("Was this suggestion useful?"));
    const wrong = section?.querySelector<HTMLInputElement>("input[type=radio][value=wrong]");
    act(() => wrong?.click());
    for (let round = 0; round < 4; round += 1) await settle();

    const posted = api.calls.find((call) => call.path === "/findings/f-1/fixes/x-1/verdict");
    expect(posted?.body).toEqual({ verdict: "wrong" });
  });

  it("a viewer sees no question", async () => {
    const { section } = await page(general({ latest: PROPOSED }), { role: "viewer" });

    expect(section?.querySelector('[data-testid="fix-verdict"]')).toBeNull();
  });
});
