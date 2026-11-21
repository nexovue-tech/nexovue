import { describe, expect, it } from "vitest";

import type { DeployLineAddition, FindingGeneralFix, ReleaseContext } from "./api";
import {
  deployAddition,
  earlierLink,
  restsOn,
  showsSection,
  tokensAndAge,
  triesLeft,
} from "./fix-page";

/**
 * F5's rules for rungs 1 and 2 on the page (plan T1.4; spec FR1; D54, AC37e): F5's
 * sentences join F28's row only where F28 states that release plainly, and the section
 * renders only for a role with a line.
 */

const ADDITION: DeployLineAddition = {
  release_id: "r-21",
  earlier: { release_id: "r-20", version: "v2.0", last_state: "finished" },
  sentences: [[{ name: "v2.0" }, { text: " ran before it." }]],
};

const statedRelease = (releaseId: string): Pick<ReleaseContext, "before"> => ({
  before: {
    release_id: releaseId,
    period_id: "p-1",
    service_name: "checkout",
    version: "v2.1",
    started_at: "2026-11-11T13:58:00Z",
    start_basis: "reported",
    basis: "both",
    last_state: "finished",
    is_return: false,
    already_running: false,
    called: null,
  },
});

const PLAIN = { cameFirst: true, dim: false };

describe("F5's addition to F28's release row (FR1)", () => {
  it("joins the row that states that very release plainly", () => {
    expect(deployAddition(ADDITION, statedRelease("r-21"), PLAIN)).toBe(ADDITION);
  });

  it("joins no other release, an older dim row, or a row naming no earlier release", () => {
    expect(deployAddition(ADDITION, statedRelease("r-other"), PLAIN)).toBeNull();
    expect(
      deployAddition(ADDITION, statedRelease("r-21"), { cameFirst: true, dim: true }),
    ).toBeNull();
    expect(
      deployAddition(ADDITION, statedRelease("r-21"), { cameFirst: false, dim: false }),
    ).toBeNull();
    expect(deployAddition(ADDITION, { before: null }, PLAIN)).toBeNull();
  });

  it("is absent while the read is loading or when the API adds nothing", () => {
    expect(deployAddition(undefined, statedRelease("r-21"), PLAIN)).toBeNull();
    expect(deployAddition(null, statedRelease("r-21"), PLAIN)).toBeNull();
  });

  it("links the earlier version's own release, by name", () => {
    expect(earlierLink(ADDITION)).toEqual({ label: "Open v2.0", releaseId: "r-20" });
    expect(earlierLink({ ...ADDITION, earlier: null })).toBeNull();
  });
});

describe("the Suggested fix section (AC37e, AC11a, FR10)", () => {
  const general = (overrides: Partial<FindingGeneralFix> = {}): FindingGeneralFix => ({
    finding_id: "f-1",
    line: null,
    offer: null,
    latest: null,
    earlier: [],
    ...overrides,
  });
  const LATEST = {
    id: "x-1",
    state: "refused" as const,
    heading: "No general suggestion",
    sentence: "The model said: the evidence is not enough.",
    content: null,
    tokens_used: 0,
    requested_at: "2026-11-21T11:00:00Z",
    finished_at: null,
  };

  it("renders only for a role with a line, the offer, or a suggestion of this finding's", () => {
    expect(showsSection(undefined)).toBe(false);
    expect(showsSection(general())).toBe(false);
    expect(showsSection(general({ line: { heading: "H", sentence: "A line." } }))).toBe(true);
    expect(showsSection(general({ latest: LATEST }))).toBe(true);
    expect(
      showsSection(
        general({ earlier: [{ id: "x-0", state: "failed", requested_at: LATEST.requested_at }] }),
      ),
    ).toBe(true);
  });
});

describe("rung 2b's own words (FR10a, FR37)", () => {
  it("names the facts an example rests on by their numbers above", () => {
    expect(restsOn([])).toBeNull();
    expect(restsOn([2])).toBe("Rests on fact 2 above.");
    expect(restsOn([1, 3, 4])).toBe("Rests on facts 1, 3 and 4 above.");
  });

  it("says tries left and tokens with an age from the caller's clock", () => {
    const now = Date.parse("2026-11-21T12:00:00Z");

    expect(triesLeft(2)).toBe("Tries left today: 2");
    expect(tokensAndAge(1460, "2026-11-21T11:56:00Z", now)).toBe("1,460 tokens · asked 4 min. ago");
  });
});
