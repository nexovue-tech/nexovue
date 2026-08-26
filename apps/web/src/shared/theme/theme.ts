/**
 * Theme selection. Dark is the default because this gets read at 2am; "system" defers
 * to the OS, which the token layer already handles by leaving [data-theme] off.
 *
 * The same key and the same values are read by the inline bootstrap script in
 * index.html, which runs before first paint. Change one, change both.
 */

export const THEME_STORAGE_KEY = "nexovue.theme";

export type ThemePreference = "system" | "light" | "dark";

const listeners = new Set<() => void>();

function readStored(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

let preference: ThemePreference = readStored();

function apply(next: ThemePreference) {
  if (next === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", next);
}

export function getThemePreference(): ThemePreference {
  return preference;
}

export function setThemePreference(next: ThemePreference): void {
  preference = next;
  apply(next);
  try {
    if (next === "system") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    // The choice still applies to this tab; it just will not survive a reload.
  }
  for (const listener of listeners) listener();
}

/** The theme actually on screen, which "system" alone cannot tell you. */
export function getResolvedTheme(): "light" | "dark" {
  if (preference !== "system") return preference;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function subscribeToTheme(listener: () => void): () => void {
  listeners.add(listener);
  const media = window.matchMedia("(prefers-color-scheme: light)");
  media.addEventListener("change", listener);
  return () => {
    listeners.delete(listener);
    media.removeEventListener("change", listener);
  };
}
