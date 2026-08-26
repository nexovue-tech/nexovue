import { Outlet } from "@tanstack/react-router";
import { useCallback, useState } from "react";

import { ErrorBoundary } from "@/shared/components/ErrorBoundary";
import { useOnlineStatus } from "@/shared/hooks/use-online-status";

import { CommandPaletteMount, useCommandPaletteShortcut } from "./CommandPaletteMount";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/**
 * The frame every signed-in screen renders inside.
 *
 * The route content sits behind its own boundary, so one broken screen loses the screen
 * and not the navigation, the project selector or the way back out.
 */
export function AppShell() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const online = useOnlineStatus();

  useCommandPaletteShortcut(openPalette);

  return (
    <div className="flex h-dvh overflow-hidden bg-page text-ink">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenCommandPalette={openPalette} />

        {online ? null : (
          <p
            role="status"
            className="border-warning-solid border-b-2 bg-warning-subtle px-4 py-1.5 text-warning-text text-xs"
          >
            Offline — nothing on screen is refreshing.
          </p>
        )}

        <main className="min-h-0 flex-1 overflow-y-auto">
          <ErrorBoundary scope="route">
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>

      <CommandPaletteMount open={paletteOpen} onClose={closePalette} />
    </div>
  );
}
