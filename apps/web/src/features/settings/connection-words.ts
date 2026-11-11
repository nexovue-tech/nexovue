import type { ConnectionView, SetupStep, UnavailableReason } from "./api";

/**
 * Every sentence `/settings/connections` and the GitHub setup screens say (F22, `design.md`,
 * *`/settings/connections`* and *GitHub setup screens*). In one place, so a test reads them
 * without drawing, and the API's words for the same refusals match these.
 */

export const UNAVAILABLE: Record<UnavailableReason, string> = {
  hosted: "Tracker connections are not available on the hosted service yet.",
  off: "Tracker connections are turned off on this install. An operator can turn them on: see the setup page.",
};

export const EMAIL_OFF = "Failure emails cannot be sent on this install.";
export const STATUS_ONLY = "Status only. Nothing is created without someone's click.";
export const NEEDS_ATTENTION_BANNER = "GitHub needs attention. Open settings.";
export const TEST_WORKS = "The connection works.";
export const DISCONNECT_LEAVES =
  "This deletes the stored key and stops all sync. The App stays in your GitHub account until you delete it there. A backup may hold a sealed copy.";
/** FR32: the step that ends it on GitHub's side (D38). */
export const DISCONNECT_STEP = "To stop it for good, delete the App in GitHub.";

/** FR32, AC114: how many synced issues a disconnect stops, from the real count. */
export function disconnectCount(syncedLinks: number): string {
  if (syncedLinks === 0) return "No issue is syncing now.";
  if (syncedLinks === 1) return "1 issue stops syncing. The issue stays in GitHub.";
  return `${syncedLinks} issues stop syncing. The issues stay in GitHub.`;
}
export const INVALID_SETUP = "This setup link is not valid. Nothing was changed.";
export const EXPIRED_SETUP = "This setup link has expired. Nothing was changed.";

export const startSentence = (organizationSlug: string) =>
  `You will go to GitHub to create a private App named nexovue-${organizationSlug}. You may need a GitHub organization owner.`;

/** What a failure means and what an admin does about it (FR44). Never a provider's text. */
const FIX: Record<string, string> = {
  unreachable:
    "nexovue could not reach GitHub. It keeps trying; check that this server can reach api.github.com.",
  timed_out: "GitHub did not answer in time. nexovue keeps trying.",
  server_error: "GitHub answered with an error on its side. nexovue keeps trying.",
  rate_limited: "GitHub asked nexovue to slow down. It waits, then tries again.",
  unauthorized:
    "GitHub refused the App's key. Replace the App key, or reinstall the App on GitHub.",
  app_not_installed: "The App is not installed. Install it on GitHub.",
  installation_ambiguous:
    "The App is installed on more than one account. Keep one installation on GitHub.",
  no_repository: "The App reaches no repository. Choose repositories on GitHub.",
  key_unreadable:
    "This install's CONNECTION_SECRET_KEY cannot open the stored key. An operator must restore the key the App was saved with, or replace the App key.",
  manifest_expired: EXPIRED_SETUP,
  manifest_refused: "GitHub did not accept this setup. Nothing was changed.",
  unexpected_answer: "GitHub answered in a way nexovue did not expect. nexovue keeps trying.",
};

export function failureFix(failure: string | null): string {
  return (failure && FIX[failure]) ?? "Something went wrong reaching GitHub. nexovue keeps trying.";
}

/** FR44's other form, for a person who cannot fix it. */
export const ASK_AN_ADMIN = "Ask an admin to reconnect GitHub. The case still works.";

export type SetupAction = "start_again" | "install" | "choose_repositories" | "test_again" | null;

export interface SetupRow {
  words: string;
  action: SetupAction;
}

/** One row per case, one action each (design, *GitHub setup screens*, AC124). */
export function setupRow(connection: ConnectionView): SetupRow {
  const step: SetupStep = connection.setup_step ?? "testing";
  switch (step) {
    case "on_github":
      return { words: "Setup did not finish on GitHub.", action: "start_again" };
    case "converting":
      return { words: "GitHub sent the App back. nexovue is fetching its key.", action: null };
    case "expired":
      return { words: EXPIRED_SETUP, action: "start_again" };
    case "refused":
      return { words: FIX.manifest_refused ?? EXPIRED_SETUP, action: "start_again" };
    case "not_installed":
      return { words: "Now choose which repositories it may reach.", action: "install" };
    case "no_repository":
      return {
        words: "The App is installed, but reaches no repository.",
        action: "choose_repositories",
      };
    case "test_failed":
      return { words: failureFix(connection.last_failure), action: "test_again" };
    case "testing":
      return { words: "Testing the connection.", action: "test_again" };
  }
}

export const ACTION_LABEL: Record<Exclude<SetupAction, null>, string> = {
  start_again: "Start again",
  install: "Install on GitHub",
  choose_repositories: "Choose repositories on GitHub",
  test_again: "Test again",
};

/** FR42, D12: what allowing a public repository makes public, said before the click. */
export const allowPublicWarning = (repository: string) =>
  `Issues nexovue creates in ${repository} will be public: anyone can read the service name, what moved, the times and the link to your console. A case synced with an issue there can be resolved by anyone who can close it. Issues already there are already public.`;
export const PUBLIC_REFUSED_UNTIL_ALLOWED =
  "Public. nexovue creates no issue here unless an admin allows it.";
export const publicAllowedSince = (since: string) => `Public. Issues allowed here since ${since}.`;
/** The mapping is optional, so an empty table is not a warning (design). */
export const NO_MAPPING =
  "No service has a repository set. Creating an issue asks for one each time.";
