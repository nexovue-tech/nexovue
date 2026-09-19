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

/**
 * Vite's own glob rather than `node:fs`, the same way `status-interpretation.test.ts`
 * reads sources: this package compiles without node types, and a structural test that
 * needed them could not live beside the code it guards.
 */
const SOURCES: Record<string, string> = import.meta.glob("/src/**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
});

function source(path: string): string {
  const found = SOURCES[`/src/${path}`];
  if (found === undefined) throw new Error(`${path} is named by this test and does not exist`);
  return found;
}

/** A draft somebody would be sorry to lose. */
const GUARDED = [
  "features/projects/ProjectSettings.tsx",
  "features/auth/account-sections.tsx",
  "features/settings/SiteKeysSection.tsx",
  "features/projects/CheckSecretsSection.tsx",
  "features/projects/CheckVariablesSection.tsx",
  "features/projects/MaintenanceWindowsSection.tsx",
  "features/synthetics/CheckForm.tsx",
  "features/synthetics/ImportScreen.tsx",
  "features/alerts/RuleEditor.tsx",
  "features/alerts/ChannelForm.tsx",
  "features/alerts/ChannelDetailScreen.tsx",
];

/** Submitted immediately, with no draft behind them. */
const UNGUARDED = [
  "features/projects/ProjectsScreen.tsx",
  "features/settings/ApiKeysScreen.tsx",
  "features/onboarding/OnboardingScreen.tsx",
  // Lists with links; every draft on them lives in ChannelForm, which is guarded.
  "features/alerts/AlertsScreen.tsx",
  "features/alerts/ChannelsScreen.tsx",
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
