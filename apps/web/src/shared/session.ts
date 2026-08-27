/**
 * What this tab remembers about signing in.
 *
 * The session itself is a server-side cookie (D7). What lives here is only the client's
 * memory around it, so the shell can redirect once and features never have to think
 * about 401 — app/query-client.ts owns that.
 */

const RETURN_TO_KEY = "nexovue.return-to";
const FIRST_SETUP_KEY = "nexovue.first-setup";

/** Remembers where the user was so login can put them back. */
export function rememberReturnTo(pathname: string): void {
  try {
    sessionStorage.setItem(RETURN_TO_KEY, pathname);
  } catch {
    // Blocked storage only costs the user a redirect back to the default screen.
  }
}

export function takeReturnTo(): string | undefined {
  try {
    const stored = sessionStorage.getItem(RETURN_TO_KEY);
    sessionStorage.removeItem(RETURN_TO_KEY);
    return stored ?? undefined;
  } catch {
    return undefined;
  }
}

/**
 * What signup issued and onboarding has to display.
 *
 * The ingest key is returned exactly once and is never retrievable again, so it has to
 * survive the hop from /signup to /onboarding — and a refresh on /onboarding, which is
 * what someone does after pasting the first variable. sessionStorage is the smallest
 * thing that survives both and dies with the tab. It is an ingest key, not a session
 * credential: it can write telemetry and read nothing.
 */
export interface FirstSetup {
  apiKey: string;
  otlpEndpoint: string;
  projectId: string;
}

export function rememberFirstSetup(setup: FirstSetup): void {
  try {
    sessionStorage.setItem(FIRST_SETUP_KEY, JSON.stringify(setup));
  } catch {
    // Without storage the key is still on screen; only a refresh loses it.
  }
}

export function readFirstSetup(): FirstSetup | undefined {
  try {
    const stored = sessionStorage.getItem(FIRST_SETUP_KEY);
    if (!stored) return undefined;
    const parsed: unknown = JSON.parse(stored);
    return isFirstSetup(parsed) ? parsed : undefined;
  } catch {
    return undefined; // Storage that is blocked or holding junk is storage we do not have.
  }
}

function isFirstSetup(value: unknown): value is FirstSetup {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.apiKey === "string" &&
    typeof candidate.otlpEndpoint === "string" &&
    typeof candidate.projectId === "string"
  );
}

export function forgetFirstSetup(): void {
  try {
    sessionStorage.removeItem(FIRST_SETUP_KEY);
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}

/** Drops every trace of the previous user from this tab. */
export function clearSession(): void {
  try {
    sessionStorage.removeItem(RETURN_TO_KEY);
    sessionStorage.removeItem(FIRST_SETUP_KEY);
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}
