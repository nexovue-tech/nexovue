import { useSyncExternalStore } from "react";

import {
  getResolvedTheme,
  getThemePreference,
  setThemePreference,
  subscribeToTheme,
  type ThemePreference,
} from "./theme";

export function useTheme() {
  const preference = useSyncExternalStore(subscribeToTheme, getThemePreference);
  const resolved = useSyncExternalStore(subscribeToTheme, getResolvedTheme);

  return { preference, resolved, setPreference: setThemePreference };
}

export type { ThemePreference };
