import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Which forms warn, and which deliberately do not.
 *
 * AC19 says a form must warn before unsaved changes are lost. Read as "every form", it
 * would put a dialog in front of creating a project — a single field, submitted
 * immediately, with nothing to lose — and that is how a warning stops being read. The
 * decision is therefore per form, and it is written here rather than left as an absence
 * somebody would restore by accident.
 *
 * Structural, like `test_provider_ports.py` on the other side of the repository: it
 * cannot prove a guard works, which `UnsavedChanges.test.ts` does. It proves the set has
 * not quietly changed.
 */

const source = (path: string) =>
  readFileSync(fileURLToPath(new URL(`../../${path}`, import.meta.url)), "utf8");

/** A draft somebody would be sorry to lose. */
const GUARDED = [
  "features/projects/ProjectSettings.tsx",
  "features/auth/ProfileScreen.tsx",
  "features/settings/SiteKeysSection.tsx",
];

/** Submitted immediately, with no draft behind them. */
const UNGUARDED = [
  "features/projects/ProjectsScreen.tsx",
  "features/settings/ApiKeysScreen.tsx",
  "features/onboarding/OnboardingScreen.tsx",
];

describe("AC19 is applied per form, not per screen", () => {
  it.each(GUARDED)("%s warns before losing a draft", (path) => {
    expect(source(path)).toContain("useUnsavedChanges");
  });

  it.each(UNGUARDED)("%s does not warn, because it has nothing to lose", (path) => {
    expect(source(path)).not.toContain("useUnsavedChanges");
  });

  it("guards every editable section of the project settings", () => {
    // Three forms on one screen — name, sampling, masking — each with its own draft
    // and its own save. One guard for the screen would warn about the wrong one.
    const settings = source("features/projects/ProjectSettings.tsx");

    expect(settings.match(/useUnsavedChanges\(/g)).toHaveLength(3);
  });
});
