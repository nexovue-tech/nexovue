import { describe, expect, it } from "vitest";

import type { DeployLineAddition, FindingFixes, ReleaseContext } from "./api";
import { deployAddition, earlierLink, showsSection } from "./fix-page";

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

describe("the Suggested fix section (AC37e, AC11a)", () => {
  const fixes = (section: FindingFixes["section"]): FindingFixes => ({
    finding_id: "f-1",
    deploy_line: null,
    brief: null,
    section,
  });

  it("renders only for a role with a line", () => {
    expect(showsSection(fixes(null))).toBe(false);
    expect(showsSection(undefined)).toBe(false);
    expect(showsSection(fixes({ heading: null, sentence: "A line." }))).toBe(true);
  });
});
