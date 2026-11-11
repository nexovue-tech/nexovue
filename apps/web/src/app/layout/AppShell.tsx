import { Outlet, useLocation, useNavigate, useRouter, useSearch } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { VerifyEmailBanner } from "@/features/auth/VerifyEmailBanner";
import { inDisplayMode } from "@/features/dashboards/dashboard-search";
import { ConnectionsBanner } from "@/features/settings/ConnectionsBanner";
import { SupportBanner } from "@/features/support/SupportBanner";
import { ErrorBoundary } from "@/shared/components/ErrorBoundary";
import { useOnlineStatus } from "@/shared/hooks/use-online-status";

import { RefreshControl } from "./RefreshControl";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** How long "Leave full screen" stays showing after the pointer last moved. */
const DISPLAY_BAR_AWAKE_MS = 3_000;

/**
 * What a dashboard keeps of the top bar in full screen (F20, FR55): the age and the paused
 * chip, which are the refresh control. "Leave full screen" is first in the tab order and
 * shows on focus or pointer movement, so a wall display carries no button.
 */
function DisplayBar({ onLeave }: { onLeave: () => void }) {
  const [awake, setAwake] = useState(false);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const wake = () => {
      setAwake(true);
      clearTimeout(timer);
      timer = setTimeout(() => setAwake(false), DISPLAY_BAR_AWAKE_MS);
    };
    window.addEventListener("pointermove", wake);
    return () => {
      window.removeEventListener("pointermove", wake);
      clearTimeout(timer);
    };
  }, []);

  return (
    <header className="flex min-h-10 shrink-0 items-center justify-between gap-4 border-line border-b bg-surface px-4 py-1.5">
      <button
        type="button"
        onClick={onLeave}
        className={
          awake
            ? "inline-flex min-h-11 items-center text-brand text-sm underline md:min-h-0"
            : "sr-only focus:not-sr-only focus:inline-flex focus:min-h-11 focus:items-center focus:text-brand focus:text-sm focus:underline md:focus:min-h-0"
        }
      >
        Leave full screen
      </button>
      <RefreshControl />
    </header>
  );
}

/**
 * The frame every signed-in screen renders inside.
 *
 * The route content sits behind its own boundary, so one broken screen loses the screen
 * and not the navigation, the project selector or the way back out.
 */
export function AppShell() {
  // Only meaningful below `md`, where the sidebar is a drawer rather than a column.
  const [navOpen, setNavOpen] = useState(false);
  const openNav = useCallback(() => setNavOpen(true), []);
  const closeNav = useCallback(() => setNavOpen(false), []);

  const online = useOnlineStatus();

  // A dashboard at `?display=1` hides the sidebar and top bar (F20, FR55, AC76).
  const router = useRouter();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const search = useSearch({ strict: false }) as Record<string, unknown>;
  const display = inDisplayMode(pathname, search);
  const leaveDisplay = useCallback(() => {
    const { display: _display, ...rest } = search;
    void navigate({
      href: `${pathname}${router.options.stringifySearch(rest)}`,
      replace: true,
    });
  }, [navigate, pathname, router, search]);

  useEffect(() => {
    if (!display) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") leaveDisplay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [display, leaveDisplay]);

  // Escape closes the drawer, the same way it closes every other overlay here.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNav();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen, closeNav]);

  return (
    // `relative` makes the frame the containing block for anything absolutely placed
    // inside a screen that is not itself positioned — a screen-reader caption among
    // them. Without it such an element is placed against the page, stretches the
    // document past the window, and scrolling drags the whole shell out of view.
    <div className="relative flex h-dvh overflow-hidden bg-page text-ink">
      {display ? null : <Sidebar open={navOpen} onNavigate={closeNav} />}

      {navOpen && !display ? (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={closeNav}
          className="fixed inset-0 z-30 bg-scrim md:hidden"
        />
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Above everything: whoever reads the screen is not the person signed in. */}
        <SupportBanner />
        {display ? <DisplayBar onLeave={leaveDisplay} /> : <TopBar onOpenNav={openNav} />}

        {online ? null : (
          <p
            role="status"
            className="border-warning-solid border-b-2 bg-warning-subtle px-4 py-1.5 text-warning-text text-xs"
          >
            Offline — nothing on screen is refreshing.
          </p>
        )}

        {/* Under the offline strip on purpose: offline is why the screen is wrong now,
            and an unverified address is not urgent enough to sit above that. */}
        <VerifyEmailBanner />
        {/* F22 FR31: a connection a person must fix, on every screen, to those who can. */}
        <ConnectionsBanner />

        <main className="min-h-0 flex-1 overflow-y-auto">
          <ErrorBoundary scope="route">
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
