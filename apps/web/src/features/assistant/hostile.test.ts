// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { openAssistant, resetAssistant } from "@/shared/assistant-context";
import { AssistantPanel } from "./AssistantPanel";
import hostile from "./hostile.fixture.json";
import { message, mount, step, stubApi, unmountAll } from "./panel.test-util";

/**
 * F4 AC15c: the hostile provider's whole answer, rendered as if every check had let it
 * through, with its search phrases on the steps. Nothing it wrote becomes a link, an image,
 * markup or a run script: it all reads as the characters it is. The fixture is
 * `HostileProvider`'s own output (held equal by `test_assistant_hostile.py`).
 */

afterEach(() => {
  unmountAll();
  resetAssistant();
  vi.unstubAllGlobals();
});

describe("the hostile provider's output, rendered", () => {
  it("has no link, image, markup or executed mark of its own", async () => {
    const alerted = vi.fn();
    vi.stubGlobal("alert", alerted);
    const shown = message({
      answer: {
        sentences: hostile.sentences.map((text: string) => ({
          text,
          label: "unchecked" as const,
          cause_warning: false,
          cites: ["s1"],
        })),
        removed_sentence_count: 0,
        not_verified: hostile.not_verified,
      },
      steps: hostile.search_phrases.map((phrase: string, index: number) =>
        step({ key: `s${index + 1}`, position: index + 1, display_arguments: phrase }),
      ),
    });
    stubApi({ inFlight: true, polls: [shown] });
    openAssistant();

    const panel = await mount(() => createElement(AssistantPanel));

    for (const text of [...hostile.sentences, ...hostile.not_verified, ...hostile.search_phrases]) {
      expect(panel.textContent).toContain(text);
    }
    expect(panel.querySelector("img, script, iframe, strong, em, code")).toBeNull();
    const external = [...panel.querySelectorAll("a")].filter(
      (link) => !link.href.startsWith(window.location.origin),
    );
    expect(external.map((link) => link.href)).toEqual([]);
    expect(
      [...panel.querySelectorAll("a")].some((link) => link.href.includes("evil.example")),
    ).toBe(false);
    expect(alerted).not.toHaveBeenCalled();
  });
});
