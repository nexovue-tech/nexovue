/**
 * The console's parameter-free screens a signed-in member can open, from the web app's own
 * route list, for the specs that visit every screen with chat's panel open (F4 T2.10b, T2.13).
 * The package is an ES module, so the list is found from `import.meta.url`, not `__dirname`.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const ROUTE_LIST = new URL(
  "../../../../apps/web/src/app/routes/console-routes.json",
  import.meta.url,
);

const ROUTES: string[] = JSON.parse(readFileSync(fileURLToPath(ROUTE_LIST), "utf8"));

/**
 * Not a member's console screen: the operator's pages, and the router's pages outside the
 * console's shell (`rootRoute`'s children in `app/router.tsx`: sign-in, sign-up, recovery,
 * an invitation, onboarding and the public setup docs), which have no top bar and no panel.
 */
const NOT_A_MEMBERS_SCREEN =
  /^\/(admin|login|signup|forgot-password|reset-password|verify-email|invite|onboarding|docs)(\/|$)/;

export const CONSOLE_SCREENS: readonly string[] = ROUTES.filter(
  (path) => !path.includes("$") && !NOT_A_MEMBERS_SCREEN.test(path),
);
