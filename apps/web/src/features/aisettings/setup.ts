/**
 * The AI page's Setup tab (UX review 2026-11-28, approved drawing D): from a provider to draft
 * pull requests, each step done or not in one sentence, and where it is set. Pure, so every
 * state is tested without a page; `SetupChecklist.tsx` reads the API into `SetupInput`.
 */

/** Where a step is set: a tab of the AI page, or the GitHub settings screen. */
export type SetupPlace = "provider" | "fixes" | "github";

export interface SetupStep {
  title: string;
  detail: string;
  done: boolean;
  /** The first step not done: the page's one filled button. */
  next: boolean;
  where: SetupPlace;
}

export interface SetupInput {
  provider: { host: string; model: string; tested: boolean; on: boolean } | null;
  issuesApp: { state: string; slug: string | null; repositories: number } | null;
  codeApp: { state: string | null; repositories: number } | null;
  locations: { set: number; total: number } | null;
  consentedFolders: number;
  consentedRepositories: readonly string[];
  draftRepositories: number;
  ciOn: boolean;
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

export function setupSteps(input: SetupInput): SetupStep[] {
  const drafts: Omit<SetupStep, "next">[] = [
    provider(input),
    issuesApp(input),
    codeApp(input),
    locations(input),
    consent(input),
    draftsStep(input),
  ];
  const firstOpen = drafts.findIndex((step) => !step.done);
  return drafts.map((step, index) => ({ ...step, next: index === firstOpen }));
}

function provider({ provider: saved }: SetupInput): Omit<SetupStep, "next"> {
  const base = { title: "AI provider", where: "provider" as const };
  if (saved === null) return { ...base, detail: "No provider yet", done: false };
  return {
    ...base,
    detail: `${saved.host}, ${saved.model} · ${saved.tested ? "tested" : "not tested yet"}`,
    done: saved.tested && saved.on,
  };
}

function issuesApp({ issuesApp: app }: SetupInput): Omit<SetupStep, "next"> {
  const base = { title: "GitHub for issues", where: "github" as const };
  if (app === null) return { ...base, detail: "Not connected", done: false };
  const working = app.state === "working";
  return {
    ...base,
    detail: working
      ? `App${app.slug ? ` ${app.slug}` : ""} on ${plural(app.repositories, "repository", "repositories")} · creates and follows issues`
      : "Connected, not working: open GitHub to see why",
    done: working,
  };
}

function codeApp({ codeApp: app }: SetupInput): Omit<SetupStep, "next"> {
  const base = { title: "GitHub for code", where: "github" as const };
  if (app === null || app.state === null) return { ...base, detail: "Not set up", done: false };
  const working = app.state === "working" && app.repositories > 0;
  return {
    ...base,
    detail: working
      ? `Nexovue suggestions App on ${plural(app.repositories, "repository", "repositories")} · reads code, opens drafts`
      : "Set up, on no repository yet",
    done: working,
  };
}

function locations({ locations: counts }: SetupInput): Omit<SetupStep, "next"> {
  const base = { title: "Where each service's code lives", where: "github" as const };
  if (counts === null || counts.total === 0) {
    return { ...base, detail: "No services seen yet", done: false };
  }
  const missing = counts.total - counts.set;
  return {
    ...base,
    detail:
      missing === 0
        ? `All ${plural(counts.total, "service", "services")} set`
        : `${counts.set} of ${plural(counts.total, "service", "services")} set · ${missing} cannot get a fix from their own code`,
    done: missing === 0,
  };
}

function consent(input: SetupInput): Omit<SetupStep, "next"> {
  const base = { title: "Owner agreed to send code", where: "fixes" as const };
  if (input.consentedFolders === 0)
    return { ...base, detail: "No owner has agreed yet", done: false };
  const repositories = input.consentedRepositories;
  const where =
    repositories.length === 1
      ? (repositories[0] ?? "")
      : plural(repositories.length, "repository", "repositories");
  return {
    ...base,
    detail: `${plural(input.consentedFolders, "folder", "folders")} of ${where}`,
    done: true,
  };
}

function draftsStep(input: SetupInput): Omit<SetupStep, "next"> {
  const base = { title: "Draft pull requests", where: "fixes" as const };
  if (input.draftRepositories === 0) {
    return {
      ...base,
      detail: "Allowed in no repository: suggestions download as a patch",
      done: false,
    };
  }
  return {
    ...base,
    detail: `Allowed in ${plural(input.draftRepositories, "repository", "repositories")} · Run CI ${input.ciOn ? "on" : "off"}`,
    done: true,
  };
}
