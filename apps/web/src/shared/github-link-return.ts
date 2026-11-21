/**
 * Where a person linking their GitHub account came from (F5 design, *GitHub account link*):
 * GitHub sends them to the console's return page, which finishes the link and puts them
 * back on the same finding. Held in this tab only; blocked storage costs them the trip
 * back, landing on their account settings instead.
 */

const LINK_RETURN_KEY = "nexovue.github-link-return";
/** Where the return page lands when nothing was remembered. */
export const LINK_FALLBACK_PATH = "/settings/account";

export interface LinkReturn {
  path: string;
  attemptId: string;
}

export function rememberLinkReturn(path: string, attemptId: string): void {
  try {
    sessionStorage.setItem(LINK_RETURN_KEY, JSON.stringify({ path, attemptId }));
  } catch {
    // Blocked storage only costs the person the trip back to the finding.
  }
}

/** The remembered place, once: a console path only, never an address elsewhere. */
export function takeLinkReturn(): LinkReturn | undefined {
  try {
    const stored = sessionStorage.getItem(LINK_RETURN_KEY);
    sessionStorage.removeItem(LINK_RETURN_KEY);
    if (!stored) return undefined;
    const parsed: unknown = JSON.parse(stored);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "path" in parsed &&
      "attemptId" in parsed &&
      typeof parsed.path === "string" &&
      typeof parsed.attemptId === "string" &&
      parsed.path.startsWith("/") &&
      !parsed.path.startsWith("//")
    ) {
      return { path: parsed.path, attemptId: parsed.attemptId };
    }
    return undefined;
  } catch {
    return undefined;
  }
}
