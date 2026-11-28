// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAssistantSubject,
  openAssistant,
  resetAssistant,
  setAssistantSubject,
  useAssistant,
} from "@/shared/assistant-context";
import { AssistantPanel } from "./AssistantPanel";
import type { MessageState } from "./api";
import {
  type Answers,
  MESSAGE_ID,
  message,
  mount,
  READY,
  settle,
  step,
  stubApi,
  unmountAll,
} from "./panel.test-util";
import {
  ANSWER_READY,
  ANSWERING,
  CHECK_IT_YOURSELF,
  LABEL_WORDS,
  LOST_CONTACT,
  NOT_VERIFIED,
  SEND_DATA,
  STANDING_WORDS,
} from "./states";

/**
 * F4 T2.11: the panel's states, each rendered (AC48), plain text only (AC17), a 403 inside
 * the panel that stops polling (AC73), stored labels and *Not verified* shown as stored
 * (AC77), a closed panel still polling and its button's words (AC78), and a failure's
 * reference, look and retry (AC103).
 */

const NOW = new Date("2026-11-12T09:31:00Z");

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  unmountAll();
  resetAssistant();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function ButtonWords() {
  const { activity } = useAssistant();
  return createElement("output", { "data-testid": "activity" }, activity);
}

async function openPanel(): Promise<HTMLElement> {
  openAssistant();
  return mount(() => createElement(AssistantPanel));
}

async function resumed(polls: Answers["polls"]): Promise<HTMLElement> {
  stubApi({ inFlight: true, polls });
  return openPanel();
}

describe("before anyone asks", () => {
  it.each([
    ["ai_off", STANDING_WORDS.ai_off(false)],
    ["chat_off", STANDING_WORDS.chat_off(false)],
    ["opted_out", STANDING_WORDS.opted_out(false)],
    ["no_data", STANDING_WORDS.no_data(false)],
  ] as const)("%s says so in one line, and offers no box", async (state, words) => {
    stubApi({ availability: { ...READY, state } });
    const panel = await openPanel();
    expect(panel.textContent).toContain(words);
    expect(panel.querySelector("textarea")).toBeNull();
  });

  it("no telemetry yet offers the way to send it, and no starters", async () => {
    stubApi({ availability: { ...READY, state: "no_data" } });
    const panel = await openPanel();
    const setup = [...panel.querySelectorAll("a")].find((a) => a.textContent === SEND_DATA);
    expect(setup?.getAttribute("href")).toBe("/onboarding");
    expect(panel.textContent).not.toContain("What is failing right now?");
  });

  it("no consent names the owners it waits for", async () => {
    stubApi({ availability: { ...READY, state: "no_consent", waiting_for: ["Ada"] } });
    const panel = await openPanel();
    expect(panel.textContent).toContain("Chat is waiting for Ada to agree.");
  });

  it("ready offers starters, the provider's label, tested or not, and questions left", async () => {
    stubApi();
    const panel = await openPanel();
    expect(panel.textContent).toContain("What is failing right now?");
    expect(panel.textContent).toContain("Answers go to a model on your own server, not tested.");
    expect(panel.textContent).toContain("47 questions left today");
    expect(panel.querySelector("textarea")).not.toBeNull();
  });
});

describe("focus (design, *Keyboard and screen readers*)", () => {
  it("opening moves focus to the box, which appears only once chat is ready", async () => {
    stubApi();
    const panel = await openPanel();
    const box = panel.querySelector("textarea");
    expect(box).not.toBeNull();
    expect(document.activeElement).toBe(box);
  });

  it("an answer arriving later does not take focus back to the box", async () => {
    stubApi();
    const panel = await openPanel();
    const close = panel.querySelector<HTMLButtonElement>("button[aria-label=Close]");
    act(() => close?.focus());
    await settle();
    expect(document.activeElement).toBe(close);
  });
});

describe("each state of a question renders (AC48)", () => {
  // The spec's *Conversation states*, word for word, apart from the module that says them.
  const SPEC: [MessageState, string][] = [
    ["answered", "checkout had 12 errors."],
    ["partial", "This answer uses only the reads listed."],
    ["abstained", "There was not enough evidence in what it read to answer."],
    ["withheld", "The answer could not be checked, so it is not shown."],
    ["stopped", "Stopped."],
    ["refused", "Your access or AI settings changed during this question."],
    ["failed", "Your AI provider did not answer."],
    [
      "over_limit",
      "Chat has paused for this month to keep room for critical finding explanations.",
    ],
    ["unavailable", "Not available right now."],
  ];
  it.each(SPEC)("%s", async (state, words) => {
    const answered = state === "answered" || state === "partial";
    const shown = message({ state, answer: answered ? message().answer : null });
    const panel = await resumed([shown]);
    const block = panel.querySelector(`[data-state="${state}"]`);
    expect(block, panel.innerHTML).not.toBeNull();
    expect(block?.textContent).toContain(words);
  });

  it("queued and running show the phase line, live steps and Stop", async () => {
    const running = message({ state: "running", phase: "asking", answer: null, steps: [step()] });
    const panel = await resumed([running]);
    expect(panel.textContent).toContain("Asking your AI provider, 60 s");
    expect(panel.textContent).toContain("Reading services");
    expect([...panel.querySelectorAll("button")].some((b) => b.textContent === "Stop")).toBe(true);
  });
});

describe("what a failure offers (AC103)", () => {
  it("names the message as its reference, a look of its own and Ask again, which only fills the box", async () => {
    const calls = stubApi({ inFlight: true, polls: [message({ state: "failed", answer: null })] });
    openAssistant();
    const panel = await mount(() => createElement(AssistantPanel));
    expect(panel.textContent).toContain(`Reference ${MESSAGE_ID}`);
    expect(panel.textContent).toContain(CHECK_IT_YOURSELF);
    const again = [...panel.querySelectorAll("button")].find((b) => b.textContent === "Ask again");
    await act(async () => again?.click());
    await settle(4);
    expect(panel.querySelector("textarea")?.value).toBe("What is failing right now?");
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
  });
});

describe("an answer is plain text with its stored labels (AC17, AC77)", () => {
  it("shows Markdown and HTML as the characters they are, and each stored label", async () => {
    const hostile = message({
      answer: {
        sentences: [
          {
            text: "**bold** [x](https://evil.example) <img src=x onerror=alert(1)>",
            label: "unchecked",
            cause_warning: true,
            cites: ["s1"],
          },
        ],
        removed_sentence_count: 1,
        not_verified: ["the database was not read"],
      },
    });
    const panel = await resumed([hostile]);
    expect(panel.textContent).toContain(
      "**bold** [x](https://evil.example) <img src=x onerror=alert(1)>",
    );
    expect(panel.querySelector("img")).toBeNull();
    expect(panel.querySelector("strong, b")).toBeNull();
    expect([...panel.querySelectorAll("a")].some((a) => a.href.includes("evil.example"))).toBe(
      false,
    );
    expect(panel.textContent).toContain(LABEL_WORDS.unchecked);
    expect(panel.textContent).toContain(NOT_VERIFIED);
    expect(panel.textContent).toContain("the database was not read");
    expect(panel.textContent).toContain("One sentence was removed");
  });

  it("no assistant module sets HTML", () => {
    const sources = import.meta.glob<string>("./*.tsx", {
      query: "?raw",
      import: "default",
      eager: true,
    });
    expect(Object.keys(sources).length).toBeGreaterThan(3);
    for (const [file, source] of Object.entries(sources)) {
      expect(source, file).not.toContain("dangerouslySetInnerHTML");
    }
  });
});

describe("polling", () => {
  it("a 403 renders inside the panel and stops polling (AC73)", async () => {
    stubApi({ inFlight: true, polls: [403] });
    const panel = await openPanel();
    expect(panel.textContent).toContain("cannot open this");
    const fetches = vi.mocked(fetch).mock.calls.length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1700));
    });
    const polled = vi
      .mocked(fetch)
      .mock.calls.slice(fetches)
      .filter(([url]) => String(url).includes(MESSAGE_ID));
    expect(polled).toEqual([]);
  });

  it("three failed polls in a row say Lost contact, with Retry", async () => {
    stubApi({ inFlight: true, polls: [500] });
    const panel = await openPanel();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 3400));
    });
    await settle(4);
    expect(panel.textContent).toContain(LOST_CONTACT);
  });

  it("a closed panel keeps reading, and the button goes Answering…, then Answer ready (AC78)", async () => {
    stubApi({
      inFlight: true,
      polls: [message({ state: "running", phase: "asking", answer: null }), message()],
    });
    const container = await mount(() =>
      createElement("div", null, createElement(AssistantPanel), createElement(ButtonWords)),
    );
    const words = () => container.querySelector("[data-testid=activity]")?.textContent;
    expect(words()).toBe("answering");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1700));
    });
    await settle(4);
    expect(words()).toBe("ready");
    expect(container.querySelector("[role=status].sr-only")?.textContent).toBe(
      `${ANSWER_READY}, 1 sentence`,
    );
    expect(ANSWERING).toBe("Answering…");
  });
});

describe("asking", () => {
  it("a refusal shows the API's sentence beside the box and keeps the draft (AC79)", async () => {
    stubApi({
      ask: { status: 409, code: "daily_limit", detail: "You have asked 50 questions today." },
    });
    const panel = await openPanel();
    const box = panel.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(box, "Why is checkout slow?");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => panel.querySelector("form")?.requestSubmit());
    await settle(6);
    expect(panel.querySelector("[role=alert]")?.textContent).toBe(
      "You have asked 50 questions today.",
    );
    expect(box.value).toBe("Why is checkout slow?");
  });
});

describe("asking about a finding (AC49)", () => {
  const FINDING = "01a1c18a-3778-726a-ac51-855da271a38a";
  const subject = { headline: "checkout is slower than normal", findingId: FINDING };

  it("offers the finding's starters; a click fills the box and sends nothing", async () => {
    const calls = stubApi();
    setAssistantSubject(subject);
    const panel = await openPanel();

    const starter = [...panel.querySelectorAll("button")].find(
      (b) => b.textContent === "What changed just before this started?",
    );
    await act(async () => starter?.click());
    await settle(2);

    expect(panel.querySelector("textarea")?.value).toBe("What changed just before this started?");
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
  });

  it("sends the finding's id with the question, in a new conversation", async () => {
    const calls = stubApi();
    setAssistantSubject(subject);
    const panel = await openPanel();
    const box = panel.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(box, "Has this happened before?");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => panel.querySelector("form")?.requestSubmit());
    await settle(6);

    const [asked] = calls.filter((call) => call.method === "POST");
    expect(asked?.path).toBe("/assistant/conversations");
    const sent = asked?.body as { context: { finding_id: string } } | undefined;
    expect(sent?.context.finding_id).toBe(FINDING);
  });

  it("forgets the finding when its page is left", async () => {
    stubApi();
    setAssistantSubject(subject);
    const panel = await openPanel();
    expect(panel.textContent).toContain("About checkout is slower than normal");

    await act(async () => clearAssistantSubject(subject));
    await settle(2);

    expect(panel.textContent).not.toContain("About checkout is slower than normal");
    expect(panel.textContent).toContain("What is failing right now?");
  });
});
