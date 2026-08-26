import { ComingSoon } from "@/shared/components/ComingSoon";

import { BARE_SCREENS, FINDINGS_FEED, SHELL_SCREENS } from "./route-manifest";

const BY_PATH = new Map(
  [FINDINGS_FEED, ...SHELL_SCREENS, ...BARE_SCREENS].map((screen) => [screen.path, screen]),
);

/**
 * Binds a route to its manifest entry. Looking the screen up by path rather than
 * repeating the copy in the route means a route can never disagree with the plan, and a
 * route added without an entry fails at startup instead of rendering an empty page.
 */
export function placeholderFor(path: string) {
  const screen = BY_PATH.get(path);
  if (!screen) throw new Error(`No screen defined for route ${path} in route-manifest.ts`);

  return function Placeholder() {
    return (
      <ComingSoon title={screen.title} description={screen.description} taskId={screen.taskId} />
    );
  };
}
