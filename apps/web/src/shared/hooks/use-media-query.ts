import { useSyncExternalStore } from "react";

/**
 * Whether a media query matches now, kept current. False where the browser cannot say
 * (tests without `matchMedia`), so the narrow, safer layout is the default.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (changed) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", changed);
      return () => list.removeEventListener("change", changed);
    },
    () => typeof window.matchMedia === "function" && window.matchMedia(query).matches,
    () => false,
  );
}
