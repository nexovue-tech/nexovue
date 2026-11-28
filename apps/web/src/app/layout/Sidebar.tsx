import { Link, useRouterState } from "@tanstack/react-router";
import { Settings } from "lucide-react";
import { OpenIncidentBadge } from "@/features/alerts/OpenIncidentBadge";
import { SignedInAs } from "@/features/auth/SignedInAs";
import { OpenFindingsBadge } from "@/features/findings/OpenFindingsBadge";
import { Wordmark } from "@/shared/components/Logo";
import { NAV_LABEL, NAV_ROW, NAV_ROW_ACTIVE, NAV_ROW_IDLE } from "@/shared/components/nav-row";
import { useIsOperator } from "@/shared/identity";

import { NAV_SECTIONS, SETTINGS_DOOR } from "../routes/route-manifest";

/** How the active screen is marked: a rule down its left edge and a weight change. */
const ACTIVE_ITEM = NAV_ROW_ACTIVE;

/**
 * Navigation as an index, not a toolbar. No icons: a list of thirteen icons is thirteen
 * things to decode, and the design doc rules icons-as-the-whole-language out.
 *
 * The active screen is marked by a rule down its left edge and a weight change, so it
 * still reads when the colour does not.
 *
 * Below `md` the sidebar is a drawer rather than a column: at 375px a fixed 13rem rail
 * takes more than half the screen and leaves the content unreadable. It slides over the
 * content instead, and closes when a link is followed.
 */
export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  // One section is nexovue's own, and it is the *only* one its operator is shown.
  // The role holds no permission inside any organization, so every telemetry and
  // settings screen would refuse them or come back empty — thirteen links to
  // nowhere (`docs/quality/console-review-2026-09-20.md`, D8). A customer is
  // shown the reverse, and never learns the platform area exists; the API refuses
  // it either way.
  const operator = useIsOperator();
  const sections = NAV_SECTIONS.filter((section) =>
    operator ? section.operatorOnly : !section.operatorOnly,
  );
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  return (
    <nav
      aria-label="Screens"
      // `md:translate-x-0` and `md:static` are what make this a plain column again on a
      // wide screen; everything before them only applies to the drawer.
      className={`fixed inset-y-0 left-0 z-40 flex w-52 shrink-0 flex-col border-line border-r bg-surface transition-transform duration-150 md:static md:translate-x-0 motion-reduce:transition-none ${
        open ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <Link
        // Home is the platform area for nexovue's own operator: `/` is the
        // customer's Findings feed, which answers "no such project" to a role
        // that holds none (`docs/changes/console-review-fixes/`).
        to={operator ? "/admin" : "/"}
        onClick={onNavigate}
        className="flex h-10 items-center border-line border-b px-4"
      >
        <Wordmark />
      </Link>

      <div className="flex-1 overflow-y-auto py-3">
        {sections.map((section) => (
          <div key={section.label} className="mb-5">
            <h2 className={NAV_LABEL}>{section.label}</h2>
            <ul>
              {section.items.map((item) => {
                // Findings holds `/`, which is matched exactly — so its entry
                // must also light up on `/findings/$findingId`, the one screen
                // of its area with an address of its own (AC55). Said here
                // rather than through `activeOptions`, which has no way to say
                // "exact, plus this other prefix".
                const alsoActive = alsoActiveOn(item.path, pathname);
                return (
                  <li key={item.path}>
                    <Link
                      to={item.path}
                      onClick={onNavigate}
                      activeOptions={{ exact: item.path === "/" }}
                      aria-current={alsoActive ? "page" : undefined}
                      // `relative` holds the badge's sr-only words inside the link; without
                      // it they escape the scroller and stretch the page.
                      // Taller on a phone, where the sidebar is a drawer and these
                      // are tap targets rather than a dense column to scan.
                      className={`${NAV_ROW}${alsoActive ? ` ${ACTIVE_ITEM}` : ""}`}
                      activeProps={{ className: ACTIVE_ITEM }}
                      inactiveProps={{ className: alsoActive ? "" : NAV_ROW_IDLE }}
                    >
                      {item.title}
                      {item.path === "/" ? <OpenFindingsBadge /> : null}
                      {item.path === "/alerts" ? <OpenIncidentBadge /> : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      {/* One account row (approved sketch G, 2026-11-28): Settings, the documentation, the
          theme and Sign out sit behind it, not in three stacked boxes. */}
      <div className="flex shrink-0 items-center border-line border-t px-3 py-2">
        <SignedInAs
          settings={
            <Link
              to={SETTINGS_DOOR}
              onClick={onNavigate}
              aria-label="Settings"
              title="Settings"
              className="grid size-11 shrink-0 place-items-center rounded text-ink-dim hover:bg-hover hover:text-ink md:size-7"
              activeProps={{ className: "text-brand-text" }}
            >
              <Settings size={15} aria-hidden="true" />
            </Link>
          }
        />
      </div>
    </nav>
  );
}

/**
 * Rows that are the reader's place without matching the address exactly: Findings is `/`
 * and also each finding; Settings opens at General and also covers every settings screen.
 */
export function alsoActiveOn(itemPath: string, pathname: string): boolean {
  if (itemPath === "/") return pathname.startsWith("/findings");
  if (itemPath.startsWith("/settings/")) return pathname.startsWith("/settings/");
  return false;
}
