/**
 * What the app knows about who is signed in.
 *
 * The session itself is a server-side cookie (D7); nothing secret lives here. This is
 * only the client's memory of it, so the shell can redirect once and features never
 * have to think about 401 — app/query-client.ts owns that.
 */

const RETURN_TO_KEY = "nexovue.return-to";

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

/** Drops every trace of the previous user from this tab. */
export function clearSession(): void {
  try {
    sessionStorage.removeItem(RETURN_TO_KEY);
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}
