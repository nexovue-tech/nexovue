import { Outlet } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";

import { VerifyEmailBanner } from "@/features/auth/VerifyEmailBanner";
import { SupportBanner } from "@/features/support/SupportBanner";
import { ErrorBoundary } from "@/shared/components/ErrorBoundary";
import { useOnlineStatus } from "@/shared/hooks/use-online-status";

import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

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
      <Sidebar open={navOpen} onNavigate={closeNav} />

      {navOpen ? (
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
        <TopBar onOpenNav={openNav} />

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

        <main className="min-h-0 flex-1 overflow-y-auto">
          <ErrorBoundary scope="route">
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
